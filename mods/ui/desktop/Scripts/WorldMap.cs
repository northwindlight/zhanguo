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
    private readonly Dictionary<string, Texture2D> _textures = new();
    private readonly Dictionary<Vector2I, JsonObject> _tiles = new();
    private readonly Dictionary<string, Color> _colors = new();
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
    public int TileCount => _tiles.Count;
    public int ArmyCount => Frame?["armies"]?.AsArray().Count ?? 0;
    public int TerrainBuilds => _artwork.TerrainBuilds;
    public int ArtworkPasses => _artwork.DrawPasses;
    public int SurfaceCount => _artwork.SurfaceCount;
    public int StructureCount => _layout?.Regions.Sum(r=>r.Structures.Sum(s=>s.Count)) ?? 0;
    public string ArtworkMode => "regional-landscape";
    public float Zoom => _scale;
    public JsonObject? SelectedTile => _tiles.GetValueOrDefault(Selected);

    public override void _Ready()
    {
        ClipContents = true;
        MouseFilter = MouseFilterEnum.Stop;
        TextureFilter = TextureFilterEnum.Nearest;
        _font = GetThemeFont("font", "Label");
        var background=new ColorRect { Color=new Color("234f5a"),ZIndex=-2,MouseFilter=MouseFilterEnum.Ignore };
        background.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(background);
        _artwork=new RegionArtwork { ZIndex=-1,LabelFont=_font };AddChild(_artwork);
        Resized += () => { if (_fit) Fit(); };
    }

    public void SetFrame(JsonObject frame)
    {
        var nextLayout=new RegionLayout(WorldLayoutProjection.FromFrame(frame),_layout);
        Frame = frame;
        _tiles.Clear();
        foreach (var item in frame["tiles"]!.AsArray())
        {
            var t = item!.AsObject();
            _tiles[new(I(t["position"]![0]), I(t["position"]![1]))] = t;
        }
        _size = _tiles.Keys.Max(p => Math.Max(p.X, p.Y)) + 1;
        string[] palette = { "9eb5cf", "e5a47c", "c69ddd", "91bd98", "e8cc83", "76bdbb", "d68c95", "ddd9af" };
        int index = 0;
        foreach (var nation in frame["names"]!.AsObject())
            _colors[nation.Key] = new Color(palette[index++ % palette.Length]);
        _layout=nextLayout;
        _artwork.SetLayout(_layout);
        if (_fit) Fit(); else UpdateView();
    }

    public void Fit()
    {
        _fit = true;
        _scale = Math.Clamp(Math.Min((Size.X-80)/(_size*Cell), (Size.Y-110)/(_size*Cell)), .01f, 4f);
        _offset = (Size - Vector2.One * (_size*Cell*_scale))/2;
        UpdateView();
    }

    public void FocusNation(string actor)
    {
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
        _artwork.Position=_offset;_artwork.Scale=Vector2.One*_scale;
        _artwork.SetDetailLevel(_scale<.22f?0:_scale<.55f?1:2);
        QueueRedraw();
    }
    public Color NationColor(string owner) => _colors.GetValueOrDefault(owner, new Color("8e9985"));
    public Vector2 CellCenter(Vector2I cell) => _offset+(new Vector2(cell.X,cell.Y)+Vector2.One*.5f)*Cell*_scale;
    private Texture2D Tex(string name)
    {
        if (!_textures.TryGetValue(name, out var value))
            _textures[name] = value = GD.Load<Texture2D>("res://assets/"+name+".png");
        return value;
    }
    private void Sprite(string name, Vector2 at, int x=0, int y=0, int w=16, int h=16, float size=1)
        => DrawTextureRectRegion(Tex(name), new Rect2(at, new Vector2(w,h)*size), new Rect2(x,y,w,h));

    public override void _Process(double delta)
    {
        _time += delta;
        if ((int)(_time*12) != (int)((_time-delta)*12)) QueueRedraw();
    }

    public override void _GuiInput(InputEvent input)
    {
        if (input is InputEventMouseButton b)
        {
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
                _offset = b.Position-point*_scale;_fit = false;UpdateView();
            }
        }
        if (input is InputEventMouseMotion motion && _drag)
        {
            _fit = false;_offset += motion.Relative;UpdateView();
        }
    }

    public override void _Draw()
    {
        if (Frame == null) return;
        DrawSetTransform(_offset, 0, Vector2.One*_scale);
        if(Borders)
            foreach(var (p,t) in _tiles)
            {
                string owner=S(t["owner"]);if(owner=="")continue;
                var at=new Vector2(p.X,p.Y)*Cell;
                Vector2I[] dirs={new(0,-1),new(1,0),new(0,1),new(-1,0)};
                Vector2[] corners={at,at+new Vector2(Cell,0),at+new Vector2(Cell,Cell),at+new Vector2(0,Cell)};
                for(int i=0;i<4;i++)
                    if(!_tiles.TryGetValue(p+dirs[i],out var neighbor)||S(neighbor["owner"])!=owner)
                        DrawLine(corners[i],corners[(i+1)%4],new Color(NationColor(owner),.8f),1.4f/_scale);
            }
        if(_tiles.ContainsKey(Selected))DrawRect(new Rect2(new Vector2(Selected.X,Selected.Y)*Cell,Vector2.One*Cell),new Color("fff1bb"),false,2/_scale);
        DrawSetTransform(Vector2.Zero);
        if(Units)
            foreach(var group in Frame["armies"]!.AsArray().Where(a=>F(a!["hp"])>0).GroupBy(a=>(I(a!["x"]),I(a["y"]),S(a["owner"]))))
            {
                var at=CellCenter(new Vector2I(group.Key.Item1,group.Key.Item2))-new Vector2(10,12);
                if(at.X < -40 || at.Y < -40 || at.X > Size.X+40 || at.Y > Size.Y+40)continue;
                var unit=group.First()!;bool mounted=S(unit["kind"]).EndsWith("cavalry");int tick=(int)(_time*5)%4;
                Sprite(mounted?"knight":"swordsman",at,mounted?tick*32:0,0,mounted?32:16,mounted?32:16,mounted?.65f:1.25f);
                DrawRect(new Rect2(at+new Vector2(1,-3),new Vector2(9,5)),NationColor(group.Key.Item3));
                DrawString(_font,at+new Vector2(17,20),group.Count().ToString(),fontSize:11,modulate:new Color("ffffff"));
                if(unit["engaged"]?.GetValue<bool>()==true)DrawArc(at+new Vector2(10,10),13,0,Mathf.Tau,24,new Color("ee8e64"),1.5f);
            }
        if(Borders && _scale<.55f)
            foreach(var country in Frame["names"]!.AsObject())
            {
                var land=_tiles.Where(t=>S(t.Value["owner"])==country.Key).Select(t=>t.Key).ToArray();
                if(land.Length==0)continue;
                var at=_offset+new Vector2((float)land.Average(p=>p.X)+.5f,(float)land.Average(p=>p.Y)+.5f)*Cell*_scale;
                DrawRect(new Rect2(at-new Vector2(12,14),new Vector2(26,27)),new Color(.08f,.14f,.15f,.85f));
                DrawString(_font,at+new Vector2(-8,6),S(country.Value),fontSize:18,modulate:NationColor(country.Key));
            }
    }
    public static string S(JsonNode? node) => node?.ToString() ?? "";
    public static int I(JsonNode? node) => node?.GetValue<int>() ?? 0;
    public static double F(JsonNode? node) => node?.GetValue<double>() ?? 0;
}
