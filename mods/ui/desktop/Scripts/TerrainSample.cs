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

// No game subprocess is started by this scene.
public partial class TerrainSample : Control
{
    private RegionMap _map = null!;
    private Label _detail = null!;
    private RegionLayout _layout = null!;
    private string? _capture;
    private bool _capturing;
    private double _elapsed;
    private readonly List<double> _frameTimes = new();
    private string _fixtureHash = "";
    private long _startupMemory;

    public override void _Ready()
    {
        try { Setup(); }
        catch (Exception e) { GD.PrintErr(e); GetTree().Quit(3); }
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
        top.AddThemeConstantOverride("separation", 12);
        AddChild(top);
        var title = new Label { Text = "战国 · 山河样板", CustomMinimumSize = new Vector2(230, 42) };
        title.AddThemeFontSizeOverride("font_size", 26); top.AddChild(title);
        AddButton(top, "全景  F", () => _map.Fit());
        AddButton(top, "山谷聚落", () => _map.Focus(1, 0));
        AddButton(top, "贫 / 富木", () => _map.Focus(1, 1));
        AddButton(top, "金矿", () => _map.Focus(1, 2));
        AddButton(top, "农田", () => _map.Focus(2, 2));
        var bounds = new CheckButton { Text = "地块边界" };
        bounds.Toggled += v => { _map.ShowBounds = v; _map.QueueRedraw(); }; top.AddChild(bounds);
        var footer = new VBoxContainer(); footer.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomWide);
        footer.OffsetTop = -54; footer.OffsetLeft = 20; footer.OffsetRight = -20; footer.OffsetBottom = -4;
        AddChild(footer);
        _detail = new Label { Text = "3 × 3 合成地区 · 固定视觉种子 · 选择地块查看样板输入" }; footer.AddChild(_detail);
        var hint = new Label { Text = "滚轮缩放 · 右键 / 中键拖动 · F 全景   |   第一阶段比例草样 · 房舍与人物为合成展示，无对局推进" };
        hint.AddThemeColorOverride("font_color", new Color("a7b698")); footer.AddChild(hint);
        _map.SelectionChanged = ShowRegion;
        string json = Godot.FileAccess.GetFileAsString("res://Samples/terrain-v1.json");
        _fixtureHash = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(json))).ToLowerInvariant();
        _layout = new RegionLayout(SampleInput.Parse(json));
        _map.SetLayout(_layout);
        _startupMemory = Process.GetCurrentProcess().WorkingSet64;
        var args = OS.GetCmdlineUserArgs();
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == "--smoke-output") _capture = args[i + 1];
    }

    private static void AddButton(BoxContainer parent, string text, Action callback)
    {
        var button = new Button { Text = text, FocusMode = FocusModeEnum.None };
        button.Pressed += callback; parent.AddChild(button);
    }

    private void ShowRegion(RegionPlan? r)
    {
        _detail.Text = r == null ? "选择地块查看样板输入" :
            $"{r.Input.Name} ({r.Input.X}, {r.Input.Y})  ·  木 {r.Input.Wood}/100 · 金 {r.Input.Gold}/100  ·  设施 {r.Structures.Count} · 树 {r.Trees.Count} · 比例人物 {r.People.Count}";
    }

    public override void _Process(double delta)
    {
        _elapsed += delta;
        if (_frameTimes.Count < 3600) _frameTimes.Add(delta * 1000);
        if (_capture != null && _elapsed > 1 && !_capturing) { _capturing = true; _ = Capture(); }
    }

    private async Task Screenshot(string path)
    {
        await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
        using var image = GetViewport().GetTexture().GetImage();
        if (image.SavePng(path) != Error.Ok) throw new IOException("无法保存窗口截图：" + path);
    }

    private async Task Capture()
    {
        try
        {
            string path = _capture!;
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
            _map.Fit(); await Screenshot(path);
            var click = _map.CellCenter(1, 0);
            _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Left, Pressed = true, Position = click });
            _map._GuiInput(new InputEventMouseButton { ButtonIndex = MouseButton.Left, Pressed = false, Position = click });
            if (_map.Selected?.Input.Name != "山谷聚落" || !_detail.Text.Contains("山谷聚落")) throw new Exception("样板地块点选失败");
            _map.Focus(1, 0);
            await Screenshot(Path.ChangeExtension(path, null) + "-close.png");
            var times = _frameTimes.OrderBy(t => t).ToArray();
            File.WriteAllText(path + ".json", JsonSerializer.Serialize(new
            {
                sample = "terrain-v1", fixture_sha256 = _fixtureHash, seed = _layout.Input.Seed,
                layout_version = _layout.Input.LayoutVersion, region_pixels = 256, visual_cells = 16,
                figure_pixels = new[] { 6, 8 }, structures = _layout.Regions.Sum(r => r.Structures.Count),
                trees = _layout.Regions.Sum(r => r.Trees.Count), people = _layout.Regions.Sum(r => r.People.Count),
                regions = _layout.Regions.Select(r => new { r.Input.Name, r.Input.X, r.Input.Y, r.Input.Wood, r.Input.Gold,
                    structures = r.Structures.Count, trees = r.Trees.Count, people = r.People.Count, gold_outcrops = r.Gold.Count }),
                platform = OS.GetName(), renderer = RenderingServer.GetVideoAdapterName(),
                measured_seconds = _elapsed, frames = times.Length, frame_ms_median = times[times.Length / 2],
                frame_ms_p95 = times[(int)((times.Length - 1) * .95)], startup_working_set_bytes = _startupMemory,
                final_working_set_bytes = Process.GetCurrentProcess().WorkingSet64,
                selection_checked = true, game_host_started = false
            }, new JsonSerializerOptions { WriteIndented = true }));
            GD.Print("TERRAIN_SAMPLE_OK " + path); GetTree().Quit();
        }
        catch (Exception e) { GD.PrintErr(e); GetTree().Quit(3); }
    }

    public override void _UnhandledKeyInput(InputEvent input)
    {
        if (input is InputEventKey key && key.Pressed && !key.Echo && key.Keycode == Key.F) _map.Fit();
    }
}
