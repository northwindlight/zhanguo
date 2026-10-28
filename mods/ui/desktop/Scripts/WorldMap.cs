using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

// The live observer uses the same landscape and retained artwork as the sample.
public partial class WorldMap : Control
{
    public JsonObject? Frame { get; private set; }
    public Action<JsonObject?>? SelectionChanged;
    public bool Borders = true;
    public bool Units = true;
    public Vector2I Selected = new(-1, -1);
    private const int Cell = RegionLayout.RegionSize;
    private readonly Dictionary<Vector2I, JsonObject> _tiles = new();
    private readonly Dictionary<string, Color> _colors = new();
    private readonly Dictionary<string, Vector2> _homeAnchors = new();
    private readonly Dictionary<string, string> _namesByActor = new();
    private string _activityActor = "", _activityText = "";
    private RegionArtwork _artwork = null!;
    private RegionLayout? _layout;
    private Vector2 _offset;
    private float _scale = .1f;
    private bool _drag;
    private int _size = 24;
    private double _time;
    private Vector2 _press;
    private bool _fit = true;
    private Font _font = null!;
    private ColorRect _backdrop=null!;
    public int TileCount => _tiles.Count;
    public int ArmyCount => Frame?["armies"]?.AsArray().Count ?? 0;
    public int TerrainBuilds => _artwork.TerrainBuilds;
    public int ArtworkPasses => _artwork.DrawPasses;
    public int SurfaceCount => _artwork.SurfaceCount;
    public int StructureCount => _layout?.Regions.Sum(r=>r.Structures.Sum(s=>s.Count)) ?? 0;
    public string ArtworkMode => "unified-pixel-world";
    public float Zoom => _scale;
    public int MountainCells=>_artwork.PixelMountainCells;
    public int WaterCells=>_artwork.PixelWaterCells;
    public int TownSceneryCount=>_layout?.Regions.Sum(r=>r.TownLots.Count)??0;
    public int TownHallCount=>_layout?.Regions.SelectMany(r=>r.Structures).Count(s=>s.Art.EndsWith("town-hall",StringComparison.Ordinal))??0;
    public int OilPatchCount=>_layout?.Regions.Sum(r=>r.OilPatches.Count)??0;
    public JsonObject? SelectedTile => _tiles.GetValueOrDefault(Selected);
    public readonly List<Footprint> HudObstacles=new();

    public override void _Ready()
    {
        ClipContents = true;
        MouseFilter = MouseFilterEnum.Stop;
        TextureFilter = TextureFilterEnum.Nearest;
        _font = GetThemeFont("font", "Label");
        var background=new ColorRect { Color=new Color("365b80"),ZIndex=-2,MouseFilter=MouseFilterEnum.Ignore };
        _backdrop=background;
        background.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(background);
        _artwork=new RegionArtwork { ZIndex=-1,LabelFont=_font };AddChild(_artwork);
        Resized += () => { if (_fit) Fit(); };
    }

    public void SetFrame(JsonObject frame)
    {
        DesktopScene.Synchronize(frame);
        var nextLayout=new RegionLayout(WorldLayoutProjection.FromFrame(frame),_layout);
        if(_layout?.GeometryKey!=nextLayout.GeometryKey)_guardAnchors.Clear();
        var live=nextLayout.Regions.ToHashSet();foreach(var stale in _walks.Keys.Where(k=>!live.Contains(k)).ToArray())_walks.Remove(stale);
        Frame = frame;
        _backdrop.Color=new Color(PlayerMode?"0e1c1c":"365b80");
        _tiles.Clear();
        foreach (var item in frame["tiles"]!.AsArray())
        {
            var t = item!.AsObject();
            _tiles[new(I(t["position"]![0]), I(t["position"]![1]))] = t;
        }
        _size = _tiles.Keys.Max(p => Math.Max(p.X, p.Y)) + 1;
        var palette=DesktopScene.NationPalette;
        int index = 0;
        foreach (var nation in frame["names"]!.AsObject())
        {
            _colors[nation.Key] = new Color(S(frame["colors"]?[nation.Key]) is {Length:>0} chosen?chosen:palette[index % palette.Length]);index++;
            _namesByActor[nation.Key] = nation.Value!.ToString();
        }
        _homeAnchors.Clear();
        var sums = new Dictionary<string, (float X, float Y, int N)>();
        foreach (var (p, t) in _tiles)
        {
            string owner = S(t["owner"]);
            if (owner == "") continue;
            var total = sums.GetValueOrDefault(owner);
            sums[owner] = (total.X + p.X, total.Y + p.Y, total.N + 1);
        }
        foreach (var (actor, sum) in sums)
            _homeAnchors[actor] = new Vector2(sum.X / sum.N + .5f, sum.Y / sum.N + .5f) * Cell;
        _layout=nextLayout;
        if(PlayerMode)_particles.Clear();
        _artwork.SetLayout(_layout);
        // Stage-reveal newly appearing construction sites (skip the first frame
        // so resumed sessions do not replay every foundation).
        var sites=nextLayout.Regions.SelectMany(r=>r.Structures).Where(s=>s.Remaining>0).Select(s=>s.Id).ToHashSet();
        if(_knownSites!=null)foreach(var id in sites)if(!_knownSites.Contains(id))_artwork.BeginConstruction(id);
        _knownSites=sites;
        if (_fit) Fit(); else UpdateView();
    }

    public void Fit()
    {
        _cameraAnchor=null;
        _fit = true;
        _scale = Math.Clamp(Math.Min((Size.X-80)/(_size*Cell), (Size.Y-110)/(_size*Cell)), .01f, 4f);
        _offset = (Size - Vector2.One * (_size*Cell*_scale))/2;
        UpdateView();
    }

    public void FocusNation(string actor)
    {
        _cameraAnchor=null;
        var land = _tiles.Where(p => S(p.Value["owner"]) == actor).Select(p => p.Key).ToArray();
        if (land.Length == 0) return;
        _fit = false;
        _scale = Math.Clamp(Math.Min((Size.X-320)/(Cell*4f),(Size.Y-100)/(Cell*3f)),.35f,.95f);
        _offset = Size/2 - new Vector2((float)land.Average(p => p.X)+.5f, (float)land.Average(p => p.Y)+.5f)*Cell*_scale;
        UpdateView();
    }

    private void UpdateView()
    {
        if(_artwork==null)return;
        _artwork.Position=_offset+_jitter;_artwork.Scale=Vector2.One*_scale;
        _artwork.SetDetailLevel(_scale<.22f?0:_scale<.55f?1:2);
        _artwork.SetWorldView(new Rect2(-(_offset+_jitter)/_scale,Size/_scale));
        QueueRedraw();
    }
    public Color NationColor(string owner) => _colors.GetValueOrDefault(owner, new Color("8e9985"));
    public string NationName(string actor) => _namesByActor.GetValueOrDefault(actor, actor);
    public void SetDisplayFont(Font font)
    {
        _font=font;_artwork.SetLabelFont(font);QueueRedraw();
    }

    // Live activity chip floating above the acting nation's territory.
    public void ShowActivity(string actor, string text)
    {
        _activityActor = actor; _activityText = text; QueueRedraw();
    }
    public void ClearActivity()
    {
        if (_activityText == "") return;
        _activityActor = ""; _activityText = ""; QueueRedraw();
    }
    private void DrawActivityChip()
    {
        if (_activityText == "" || Frame == null) return;
        if (!_homeAnchors.TryGetValue(_activityActor, out var anchor)) return;
        var color = NationColor(_activityActor);
        string text = $"{NationName(_activityActor)}{_activityText}";
        var at = Screen(anchor) + new Vector2(0, -30 * MathF.Max(.6f, _scale));
        var size = _font.GetStringSize(text, fontSize: 16);
        var rect = new Rect2(at - new Vector2(size.X / 2 + 10, size.Y + 8), size + new Vector2(20, 14));
        DrawRect(new Rect2(rect.Position + new Vector2(0, 2), rect.Size), new Color(0, 0, 0, .25f));
        DrawRect(rect, new Color(.05f, .09f, .10f, .92f));
        DrawRect(new Rect2(rect.Position, new Vector2(3, rect.Size.Y)), color);
        // Spinner: three dots chase while the model works.
        for (int i = 0; i < 3; i++)
        {
            float phase = (float)(_time * 2.4f + i * .33f) % 1f;
            float alpha = 1f - MathF.Abs(phase - .5f) * 2f;
            DrawCircle(rect.Position + new Vector2(rect.Size.X - 8 - i * 7, 9), 2.2f,
                new Color(color, .25f + .75f * alpha));
        }
        DrawString(_font, rect.Position + new Vector2(9, size.Y + 5), text, fontSize: 16, modulate: new Color("f2ecd2"));
        var tip = Screen(anchor) + new Vector2(0, -8);
        var left = tip + new Vector2(-5, 0);
        var right = tip + new Vector2(5, 0);
        var baseLeft = rect.Position + new Vector2(rect.Size.X * .5f - 5, rect.Size.Y);
        var baseRight = rect.Position + new Vector2(rect.Size.X * .5f + 5, rect.Size.Y);
        if (baseLeft.Y - left.Y > 1f)
            DrawColoredPolygon(new[] { left, right, baseRight, baseLeft }, new Color(.05f, .09f, .10f, .92f));
    }

    public Vector2 CellCenter(Vector2I cell) => _offset+_jitter+(new Vector2(cell.X,cell.Y)+Vector2.One*.5f)*Cell*_scale;
    public override void _Process(double delta)
    {
        _time += delta;
        if(_shakeAmp>.02f)
        {
            _shakeAmp*=MathF.Exp(-(float)delta*2.2f);
            _jitter=new Vector2(MathF.Sin((float)_time*43),MathF.Cos((float)_time*37))*3*_shakeAmp;
            UpdateView();
        }
        else if(_jitter!=Vector2.Zero){_jitter=Vector2.Zero;UpdateView();}
        if(_cameraAnchor is Vector2 anchor)
        {
            float previousScale=_scale;
            _scale+=(_cameraScale-_scale)*(1-MathF.Exp(-(float)delta*4));
            var focus=_beat?["ledger"]!=null?new Vector2(Math.Max(340,Size.X*.5f-160),Size.Y*.53f):Size*.5f;
            if(PlayerMode&&PlayerPanelOpen)focus=new Vector2(Math.Max(340,(Size.X-540)*.5f),Size.Y*.56f);
            if(_beat?["letter"]!=null)focus.X+=120;
            var center=(focus-_offset)/Math.Max(.01f,previousScale);
            center=center.Lerp(anchor,1-MathF.Exp(-(float)delta*5));
            var target=focus-anchor*_scale;
            _offset=focus-center*_scale;
            if(_offset.DistanceTo(target)<.5f&&MathF.Abs(_scale-_cameraScale)<.005f)
            {_offset=target;_scale=_cameraScale;_cameraAnchor=null;}
            UpdateView();
        }
        if ((int)(_time*24) != (int)((_time-delta)*24)) { AmbientTick(); QueueRedraw(); }
    }

    public override void _GuiInput(InputEvent input)
    {
        if(input is InputEventMouseMotion hover)HoverHumanTarget(hover.Position);
        if (input is InputEventMouseButton b)
        {
            if(b.Pressed)_cameraAnchor=null;
            if(b.Pressed&&b.ButtonIndex==MouseButton.Right)
            {
                var point=(b.Position-_offset)/(_scale*Cell);
                if(HumanMapOrder?.Invoke(new((int)MathF.Floor(point.X),(int)MathF.Floor(point.Y)),b.CtrlPressed,b.ShiftPressed)==true)
                { _drag=false;AcceptEvent();return; }
            }
            if (b.ButtonIndex is MouseButton.Middle or MouseButton.Right) _drag = b.Pressed;
            if (b.ButtonIndex == MouseButton.Left)
            {
                if (b.Pressed) _press = b.Position;
                else if (_press.DistanceTo(b.Position) < 5)
                {
                    var p = (b.Position-_offset)/(_scale*Cell);
                    Selected = new((int)MathF.Floor(p.X), (int)MathF.Floor(p.Y));
                    SelectionChanged?.Invoke(_tiles.GetValueOrDefault(Selected));
                    QueueRedraw();
                }
            }
            if (b.Pressed && b.ButtonIndex is MouseButton.WheelUp or MouseButton.WheelDown)
            {
                var point = (b.Position-_offset)/_scale;
                _scale = Math.Clamp(_scale * (b.ButtonIndex == MouseButton.WheelUp ? 1.18f : 1/1.18f), .025f, 3f);
                _offset = b.Position-point*_scale;_fit = false;_cameraAnchor=null;UpdateView();
            }
        }
        if (input is InputEventMouseMotion motion && _drag)
        {
            _cameraAnchor=null;
            _fit = false;_offset += motion.Relative;UpdateView();
        }
    }

    public override void _Draw()
    {
        if (Frame == null) return;
        DrawSetTransform(_offset+_jitter, 0, Vector2.One*_scale);
        if(Borders)
            foreach(var (p,t) in _tiles)
            {
                string owner=S(t["owner"]);if(owner=="")continue;
                var at=new Vector2(p.X,p.Y)*Cell;
                DrawRect(new Rect2(at,Vector2.One*Cell),new Color(NationColor(owner),PoliticalOpacity));
                Vector2I[] dirs={new(0,-1),new(1,0),new(0,1),new(-1,0)};
                Vector2[] corners={at,at+new Vector2(Cell,0),at+new Vector2(Cell,Cell),at+new Vector2(0,Cell)};
                for(int i=0;i<4;i++)
                    if(!_tiles.TryGetValue(p+dirs[i],out var neighbor)||S(neighbor["owner"])!=owner)
                        DrawLine(corners[i],corners[(i+1)%4],new Color(NationColor(owner),.8f),1.4f/_scale);
            }
        if(_tiles.ContainsKey(Selected))DrawRect(new Rect2(new Vector2(Selected.X,Selected.Y)*Cell,Vector2.One*Cell),new Color("fff1bb"),false,2/_scale);
        DrawSetTransform(Vector2.Zero);
        DrawResidents();DrawTroops();DrawBeatEffects();DrawAmbient();DrawActivityChip();
        if(Borders && _scale<.55f)
            foreach(var country in Frame["names"]!.AsObject())
            {
                var land=_tiles.Where(t=>S(t.Value["owner"])==country.Key).Select(t=>t.Key).ToArray();
                if(land.Length==0)continue;
                var at=_offset+_jitter+new Vector2((float)land.Average(p=>p.X)+.5f,(float)land.Average(p=>p.Y)+.5f)*Cell*_scale;
                DrawRect(new Rect2(at-new Vector2(12,14),new Vector2(26,27)),new Color(.08f,.14f,.15f,.85f));
                DrawString(_font,at+new Vector2(-8,6),S(country.Value),fontSize:18,modulate:NationColor(country.Key));
            }
        DrawHumanFog();
    }
    public static string S(JsonNode? node) => node?.ToString() ?? "";
    public static int I(JsonNode? node) => node?.GetValue<int>() ?? 0;
    public static double F(JsonNode? node) => double.TryParse(node?.ToString(),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out double value)?value:0;
}
