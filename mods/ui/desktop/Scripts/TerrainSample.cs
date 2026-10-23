using Godot;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Security.Cryptography;
using System.Collections.Generic;
using System.Threading.Tasks;
using Zhanguo.Desktop.Layout;

// Standalone synthetic samples. No game subprocess is started by this scene.
public partial class TerrainSample : Control
{
    private RegionMap _map = null!;
    private Label _detail = null!;
    private RegionLayout _layout = null!;
    private RegionLayout _showcase = null!;
    private RegionLayout _neighbors = null!;
    private string? _capture;
    private bool _capturing;
    private bool _tour;
    private bool _tourEnding;
    private double _tourStart = double.NaN;
    private int _tourPhase = -1;
    private readonly Stopwatch _watch = Stopwatch.StartNew();
    private long _lastTick;
    private readonly List<double> _frameTimes = new();
    private readonly List<object> _memorySamples = new();
    private int _baselineNodes;
    private int _baselinePasses;
    private int _baselineBuilds;
    private int _nextMemorySecond;
    private long _startupMemory;
    private double _warmupSeconds;
    private bool _clippingChecked;
    private int _clipSamples;
    private readonly Dictionary<string, string> _fixtureHashes = new();

    public override void _Ready()
    {
        try { Setup(); }
        catch (Exception e) { GD.PrintErr(e); GetTree().Quit(3); }
    }

    private RegionLayout ReadLayout(string name)
    {
        string json = Godot.FileAccess.GetFileAsString("res://Samples/" + name + ".json");
        _fixtureHashes[name] = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(json))).ToLowerInvariant();
        return new RegionLayout(SampleInput.Parse(json));
    }

    private void SelectLayout(RegionLayout layout)
    {
        _layout = layout; _map.SetLayout(layout);
        _detail.Text = ReferenceEquals(layout, _neighbors)
            ? "邻接样板 · 山 / 林 / 丘陵 / 平原 · 固定输入与种子"
            : "主样板 · 山谷 / 四档木量 / 金矿 / 农田 · 固定输入与种子";
    }

    private void Setup()
    {
        Theme = new Theme
        {
            DefaultFontSize = 15,
            DefaultFont = new SystemFont { FontNames = new[] { "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "sans-serif" } }
        };
        _map = new RegionMap();
        _map.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _map.OffsetTop = 62; _map.OffsetBottom = -58;
        AddChild(_map);
        var top = new HBoxContainer { Position = new Vector2(18, 10) };
        top.AddThemeConstantOverride("separation", 10); AddChild(top);
        var title = new Label { Text = "战国 · 山河样板", CustomMinimumSize = new Vector2(220, 42) };
        title.AddThemeFontSizeOverride("font_size", 26); top.AddChild(title);
        AddButton(top, "全景 F", () => _map.Fit());
        AddButton(top, "山谷", () => { SelectLayout(_showcase); _map.Focus(1, 0); });
        AddButton(top, "林缘", () => { SelectLayout(_showcase); _map.Focus(1, 1); });
        AddButton(top, "金矿", () => { SelectLayout(_showcase); _map.Focus(1, 2); });
        AddButton(top, "农田", () => { SelectLayout(_showcase); _map.Focus(2, 2); });
        AddButton(top, "主样板", () => SelectLayout(_showcase));
        AddButton(top, "邻接对照", () => SelectLayout(_neighbors));
        var cliffs = new CheckButton { Text = "岩壁", ButtonPressed = true };
        cliffs.Toggled += v => _map.ShowCliffs = v; top.AddChild(cliffs);
        var bounds = new CheckButton { Text = "地块边界" };
        bounds.Toggled += v => { _map.ShowBounds = v; _map.QueueRedraw(); }; top.AddChild(bounds);
        var footer = new VBoxContainer(); footer.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomWide);
        footer.OffsetTop = -54; footer.OffsetLeft = 20; footer.OffsetRight = -20; footer.OffsetBottom = -4;
        AddChild(footer);
        _detail = new Label(); footer.AddChild(_detail);
        var hint = new Label { Text = "滚轮缩放 · 右键 / 中键拖动 · F 全景   |   第一阶段合成样板 · 无对局推进" };
        hint.AddThemeColorOverride("font_color", new Color("a7b698")); footer.AddChild(hint);
        _map.SelectionChanged = ShowRegion;
        _showcase = ReadLayout("terrain-v2"); _neighbors = ReadLayout("terrain-neighbors-v2");
        SelectLayout(_showcase);
        _startupMemory = Process.GetCurrentProcess().WorkingSet64;
        _lastTick = Stopwatch.GetTimestamp();
        var args = OS.GetCmdlineUserArgs();
        _tour = args.Contains("--sample-tour");
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == "--smoke-output") _capture = args[i + 1];
    }

    private static void AddButton(BoxContainer parent, string text, Action callback)
    {
        var button = new Button { Text = text, FocusMode = FocusModeEnum.None };
        button.Pressed += callback; parent.AddChild(button);
    }

    private void ShowRegion(RegionPlan? r)
    {
        _detail.Text = r == null ? "选择地块查看合成输入" :
            $"{r.Input.Name} ({r.Input.X}, {r.Input.Y})  ·  木 {r.Input.Wood}/100 · 金 {r.Input.Gold}/100  ·  设施 {r.Structures.Count} · 树 {r.Trees.Count} · 比例人物 {r.People.Count}";
    }

    private static int CountNodes(Node root) => 1 + root.GetChildren().Sum(n => CountNodes(n));

    public override void _Process(double delta)
    {
        long tick = Stopwatch.GetTimestamp();
        double frameMs = (tick - _lastTick) * 1000.0 / Stopwatch.Frequency; _lastTick = tick;
        if (!double.IsNaN(_tourStart))
        {
            double t = _watch.Elapsed.TotalSeconds - _tourStart;
            if (t >= 0 && !_tourEnding)
            {
                if (_frameTimes.Count < 8192) _frameTimes.Add(frameMs);
                if (t >= _nextMemorySecond)
                {
                    _memorySamples.Add(new { seconds = t, working_set_bytes = Process.GetCurrentProcess().WorkingSet64,
                        managed_bytes = GC.GetTotalMemory(false), nodes = CountNodes(this), textures = _map.TextureCount,
                        surfaces = _map.SurfaceCount, terrain_builds = _map.TerrainBuilds, artwork_passes = _map.ArtworkPasses });
                    _nextMemorySecond += 5;
                }
                if (t >= 32)
                {
                    _tourEnding = true;
                    if (CountNodes(this) != _baselineNodes || _map.TerrainBuilds != _baselineBuilds || _map.SurfaceCount != 2)
                    { Fail(new Exception("巡览期间节点或地貌缓存异常增长")); return; }
                    WriteReport(t); GD.Print("TERRAIN_TOUR_OK " + _capture); GetTree().Quit(); return;
                }
                MoveTourCamera(t);
            }
        }
        else if (_capture != null && _watch.Elapsed.TotalSeconds > 1 && !_capturing)
        { _capturing = true; _ = Capture(); }
    }

    private void MoveTourCamera(double t)
    {
        int phase = Math.Min(5, (int)(t / 5.5));
        if (phase != _tourPhase)
        {
            _tourPhase = phase;
            if (phase == 0) { SelectLayout(_showcase); _map.Fit(); }
            else if (phase < 4) _map.Focus(1, phase - 1);
            else if (phase == 4) { SelectLayout(_neighbors); _map.Fit(); }
            else _map.Focus(1, 1);
        }
        // Exercise the same drag handler as a real right-button mouse motion.
        var p = new Vector2(600, 400);
        _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Right, Pressed = true, Position = p });
        _map._GuiInput(new InputEventMouseMotion { Position = p, Relative = new Vector2((float)Math.Sin(t * .7) * .6f, (float)Math.Cos(t * .5) * .3f) });
        _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Right, Pressed = false, Position = p });
    }

    private async Task Screenshot(string path, bool checkClipping = false)
    {
        await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
        using var image = GetViewport().GetTexture().GetImage();
        if (checkClipping)
        {
            var rect = _map.WorldScreenRect;
            int top = (int)rect.Position.Y - 4;
            int left = (int)rect.Position.X - 4;
            var expected = new Color("1c2b29");
            void Check(int x, int y)
            {
                var c = image.GetPixel(x, y);
                if (Math.Abs(c.R - expected.R) + Math.Abs(c.G - expected.G) + Math.Abs(c.B - expected.B) > .035f)
                    throw new Exception($"地图外缘漏绘 ({x}, {y})");
                _clipSamples++;
            }
            for (int x = (int)rect.Position.X + 8; x < rect.End.X - 8; x += 3) Check(x, top);
            for (int y = (int)rect.Position.Y + 8; y < rect.End.Y - 80; y += 3) Check(left, y);
            _clippingChecked = true;
        }
        if (image.SavePng(path) != Error.Ok) throw new IOException("无法保存窗口截图：" + path);
    }

    private async Task Capture()
    {
        try
        {
            string path = _capture!; string stem = Path.ChangeExtension(path, null);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
            _map.Fit(); await Screenshot(path, true);
            var click = _map.CellCenter(1, 0);
            _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Left, Pressed = true, Position = click });
            _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Left, Pressed = false, Position = click });
            if (_map.Selected?.Input.Name != "山谷聚落" || !_detail.Text.Contains("山谷聚落")) throw new Exception("样板地块点选失败");
            _map.Focus(1, 0); await Screenshot(stem + "-close.png");
            _map.Focus(1, 1); await Screenshot(stem + "-forest.png");
            _map.Focus(1, 2); await Screenshot(stem + "-gold.png");
            SelectLayout(_neighbors); await Screenshot(stem + "-neighbors.png", true);
            _map.Focus(1, 1); await Screenshot(stem + "-neighbors-close.png");
            SelectLayout(_showcase); _map.Fit();
            await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
            if (!_tour) { WriteReport(0); GD.Print("TERRAIN_SAMPLE_OK " + path); GetTree().Quit(); return; }
            // Both layouts and all textures have been drawn before the warm-up.
            double warmupStart = _watch.Elapsed.TotalSeconds;
            await ToSignal(GetTree().CreateTimer(5), SceneTreeTimer.SignalName.Timeout);
            _warmupSeconds = _watch.Elapsed.TotalSeconds - warmupStart;
            _baselineNodes = CountNodes(this); _baselinePasses = _map.ArtworkPasses; _baselineBuilds = _map.TerrainBuilds;
            _frameTimes.Clear(); _lastTick = Stopwatch.GetTimestamp();
            _tourStart = _watch.Elapsed.TotalSeconds + 1.5;
            File.WriteAllText(path + ".tour-start.json", "{\"duration_seconds\":32,\"recorder_lead_seconds\":1.5}");
        }
        catch (Exception e) { Fail(e); }
    }

    private static object LayoutSummary(RegionLayout layout) => new
    {
        structures = layout.Regions.Sum(r => r.Structures.Count), trees = layout.Regions.Sum(r => r.Trees.Count),
        people = layout.Regions.Sum(r => r.People.Count), rocks = layout.Regions.Sum(r => r.Rocks.Count),
        mountain_outcrops = layout.Regions.Sum(r => r.Outcrops.Count),
        regions = layout.Regions.Select(r => new { r.Input.Name, r.Input.X, r.Input.Y, r.Input.Wood, r.Input.Gold,
            structures = r.Structures.Count, trees = r.Trees.Count, people = r.People.Count, gold_outcrops = r.Gold.Count })
    };

    private void WriteReport(double measured)
    {
        var times = _frameTimes.OrderBy(t => t).ToArray();
        File.WriteAllText(_capture + ".json", JsonSerializer.Serialize(new
        {
            sample = "terrain-v2", fixture_sha256 = _fixtureHashes, layout_version = _showcase.Input.LayoutVersion,
            seed = _showcase.Input.Seed, region_pixels = 256, visual_cells = 16, figure_pixels = new[] { 6, 8 },
            showcase = LayoutSummary(_showcase), neighbors = LayoutSummary(_neighbors),
            active_fixture = ReferenceEquals(_layout, _neighbors) ? "terrain-neighbors-v2" : "terrain-v2",
            platform = OS.GetName(), renderer = RenderingServer.GetVideoAdapterName(),
            tour = _tour, warmup_seconds = _warmupSeconds, measured_seconds = measured,
            frame_clock = "Stopwatch wall clock between process frames; captures excluded from tour window",
            frames = times.Length, frame_ms_median = times.Length > 0 ? times[times.Length / 2] : (double?)null,
            frame_ms_p95 = times.Length > 0 ? times[(int)((times.Length - 1) * .95)] : (double?)null,
            frame_ms_max = times.Length > 0 ? times[^1] : (double?)null,
            startup_working_set_bytes = _startupMemory, final_working_set_bytes = Process.GetCurrentProcess().WorkingSet64,
            baseline_nodes = _baselineNodes, final_nodes = CountNodes(this),
            terrain_builds = _map.TerrainBuilds, surface_cache_count = _map.SurfaceCount, texture_cache_count = _map.TextureCount,
            tour_artwork_passes = _tour ? _map.ArtworkPasses - _baselinePasses : _map.ArtworkPasses,
            cliff_edges = _map.CliffCount, front_cliff_faces = _map.FrontFaceCount,
            memory_samples = _memorySamples, selection_checked = true,
            world_clipping_checked = _clippingChecked, world_clip_pixel_samples = _clipSamples, game_host_started = false
        }, new JsonSerializerOptions { WriteIndented = true }));
    }

    private void Fail(Exception e) { GD.PrintErr(e); GetTree().Quit(3); }

    public override void _UnhandledKeyInput(InputEvent input)
    {
        if (input is InputEventKey key && key.Pressed && !key.Echo && key.Keycode == Key.F) _map.Fit();
    }
}
