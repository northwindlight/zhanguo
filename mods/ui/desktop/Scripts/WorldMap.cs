using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

public partial class WorldMap : Control
{
    public JsonObject? Frame { get; private set; }
    public Action<JsonObject?>? SelectionChanged;
    public bool Borders = true;
    public bool Units = true;
    public Vector2I Selected = new(-1, -1);
    private readonly Dictionary<string, Texture2D> _textures = new();
    private readonly Dictionary<Vector2I, JsonObject> _tiles = new();
    private readonly Dictionary<string, Color> _colors = new();
    private Vector2 _offset;
    private float _scale = 1;
    private bool _drag;
    private int _size = 24;
    private double _time;
    private Vector2 _press;
    private bool _fit = true;
    private Font _font = null!;
    public int TileCount => _tiles.Count;
    public int ArmyCount => Frame?["armies"]?.AsArray().Count ?? 0;

    public override void _Ready()
    {
        ClipContents = true;
        MouseFilter = MouseFilterEnum.Stop;
        TextureFilter = TextureFilterEnum.Nearest;
        _font = GetThemeFont("font", "Label");
        Resized += () => { if (_fit) Fit(); };
    }

    public void SetFrame(JsonObject frame)
    {
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
        if (_fit) Fit();
        SelectionChanged?.Invoke(_tiles.GetValueOrDefault(Selected));
        QueueRedraw();
    }

    public void Fit()
    {
        _fit = true;
        _scale = Math.Clamp(Math.Min((Size.X-70)/(_size*32), (Size.Y-70)/(_size*32)), .3f, 4f);
        _offset = (Size - Vector2.One * (_size*32*_scale))/2;
        QueueRedraw();
    }

    public void FocusNation(string actor)
    {
        var land = _tiles.Where(p => S(p.Value["owner"]) == actor).Select(p => p.Key).ToArray();
        if (land.Length == 0) return;
        _fit = false;
        _scale = 2.5f;
        _offset = Size/2 - new Vector2((float)land.Average(p => p.X)+.5f, (float)land.Average(p => p.Y)+.5f)*32*_scale;
        QueueRedraw();
    }

    public Color NationColor(string owner) => _colors.GetValueOrDefault(owner, new Color("8e9985"));
    public Vector2 CellCenter(Vector2I cell) => _offset+(new Vector2(cell.X,cell.Y)+Vector2.One*.5f)*32*_scale;
    private Texture2D Tex(string name)
    {
        if (!_textures.TryGetValue(name, out var value))
        {
            value = GD.Load<Texture2D>("res://assets/"+name+".png");
            _textures[name] = value;
        }
        return value;
    }

    private void Sprite(string name, Vector2 at, int x=0, int y=0, int w=16, int h=16, float size=1, Color? color=null)
    {
        DrawTextureRectRegion(Tex(name), new Rect2(at, new Vector2(w,h)*size), new Rect2(x,y,w,h), color);
    }

    public override void _Process(double delta)
    {
        _time += delta;
        // Redraw at 12 Hz; the camera and other controls still run each frame.
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
                    var p = (b.Position-_offset)/(_scale*32);
                    Selected = new((int)MathF.Floor(p.X), (int)MathF.Floor(p.Y));
                    SelectionChanged?.Invoke(_tiles.GetValueOrDefault(Selected));
                    QueueRedraw();
                }
            }
            if (b.Pressed && b.ButtonIndex is MouseButton.WheelUp or MouseButton.WheelDown)
            {
                var point = (b.Position-_offset)/_scale;
                _scale = Math.Clamp(_scale * (b.ButtonIndex == MouseButton.WheelUp ? 1.18f : 1/1.18f), .3f, 5f);
                _offset = b.Position-point*_scale;
                _fit = false;
                QueueRedraw();
            }
        }
        if (input is InputEventMouseMotion motion && _drag)
        {
            _fit = false;
            _offset += motion.Relative;
            QueueRedraw();
        }
    }

    public override void _Draw()
    {
        DrawRect(new Rect2(Vector2.Zero, Size), new Color("182323"));
        if (Frame == null) return;
        DrawSetTransform(_offset, 0, Vector2.One*_scale);
        DrawRect(new Rect2(-5,-5,_size*32+10,_size*32+10), new Color("6e765c"));
        foreach (var (p,t) in _tiles)
        {
            var at = new Vector2(p.X,p.Y)*32;
            var terrain = S(t["terrain"]);
            int hash = (p.X*71+p.Y*133)%17;
            int ground = terrain.EndsWith("desert") ? 4 : terrain.EndsWith("mountain") ? 1 : 2;
            Sprite("grass",at,ground*16,0,16,16,2);
            DrawRect(new Rect2(at,Vector2.One*32),new Color(0,0,0,hash*.002f));
            if (terrain.EndsWith("forest"))
            {
                for (int i=0;i<3;i++)
                    Sprite("trees",at+new Vector2(2+i*8,2+(i%2)*9),16*(1+(hash+i)%3),0,16,16,1.15f);
            }
            else if (terrain.EndsWith("mountain") || terrain.EndsWith("hill"))
            {
                float h = terrain.EndsWith("mountain") ? 24 : 13;
                DrawColoredPolygon(new[] {at+new Vector2(1,29),at+new Vector2(16,29-h),at+new Vector2(31,29)},new Color("7e8977"));
                DrawColoredPolygon(new[] {at+new Vector2(16,29-h),at+new Vector2(19,29),at+new Vector2(31,29)},new Color("626f66"));
                if(h>20) DrawColoredPolygon(new[] {at+new Vector2(12,11),at+new Vector2(16,5),at+new Vector2(20,12)},new Color("c5c7ad"));
            }
            else if(hash%3==0) Sprite("trees",at+new Vector2(18,15),16,0,16,16,.7f);
            var owner=S(t["owner"]);
            if (owner!="" && Borders)
                DrawRect(new Rect2(at,Vector2.One*32),new Color(NationColor(owner),.14f));
            int n=0;
            foreach(var b in t["buildings"]!.AsArray())
            {
                string kind=S(b![0]);
                var pos=at+new Vector2(1+(n%2)*14,3+(n/2)*10);
                if (kind.EndsWith("farm")) Sprite("wheat",pos,0,0,16,16,1);
                else Sprite(kind.Contains("barrack")?"barracks":"houses",pos,0,0,16,16,1);
                n++;
                if(n>=4)break;
            }
            if (_scale>1.4f && owner!="")
                DrawString(_font,at+new Vector2(1,31),S(Frame["names"]![owner]),HorizontalAlignment.Left,-1,9,new Color("fcf3cf"));
        }
        // Draw boundaries after every terrain tile so neighboring fills cannot
        // overwrite a shared edge. Keep their screen width stable when zooming.
        if(Borders)
        {
            foreach(var (p,t) in _tiles)
            {
                string owner=S(t["owner"]);if(owner=="")continue;
                var at=new Vector2(p.X,p.Y)*32;
                Vector2I[] dirs={new(0,-1),new(1,0),new(0,1),new(-1,0)};
                Vector2[] corners={at,at+new Vector2(32,0),at+new Vector2(32,32),at+new Vector2(0,32)};
                for(int i=0;i<4;i++)
                    if(!_tiles.TryGetValue(p+dirs[i],out var neighbor)||S(neighbor["owner"])!=owner)
                        DrawLine(corners[i],corners[(i+1)%4],NationColor(owner),2/_scale);
            }
        }
        if(Units)
        {
            foreach(var grouping in Frame["armies"]!.AsArray().Where(a=>F(a!["hp"])>0).GroupBy(a=>(I(a!["x"]),I(a["y"]),S(a["owner"]))))
            {
                var at=new Vector2(grouping.Key.Item1,grouping.Key.Item2)*32+new Vector2(8,8);
                var unit=grouping.First()!;
                bool mounted=S(unit["kind"]).EndsWith("cavalry");
                // Only the documented idle row plays here; combat needs event choreography.
                int tick=(int)(_time*5)%4;
                Sprite(mounted?"knight":"swordsman",at,mounted?tick*32:0,0,mounted?32:16,mounted?32:16,mounted?.72f:1.4f);
                var color=NationColor(grouping.Key.Item3);
                DrawLine(at+new Vector2(0,-2),at+new Vector2(0,12),new Color("e7dfb9"),1);
                DrawRect(new Rect2(at+new Vector2(1,-2),new Vector2(9,5)),color);
                DrawString(_font,at+new Vector2(18,22),grouping.Count().ToString(),HorizontalAlignment.Left,-1,10,new Color("ffffff"));
                if(unit["engaged"]?.GetValue<bool>()==true)
                    DrawArc(at+new Vector2(12,12),13,0,Mathf.Tau,24,new Color("ee8e64"),1.5f);
            }
        }
        if(_tiles.ContainsKey(Selected)) DrawRect(new Rect2(new Vector2(Selected.X,Selected.Y)*32,Vector2.One*32),new Color("fff1bb"),false,2);
        DrawSetTransform(Vector2.Zero);
        if (Borders && _scale < 1.4f)
        {
            foreach (var country in Frame["names"]!.AsObject())
            {
                var land=_tiles.Where(t=>S(t.Value["owner"])==country.Key).Select(t=>t.Key).ToArray();
                if(land.Length==0)continue;
                var at=_offset+new Vector2((float)land.Average(p=>p.X)+.5f,(float)land.Average(p=>p.Y)+.5f)*32*_scale;
                DrawRect(new Rect2(at-new Vector2(10,13),new Vector2(23,24)),new Color("172522"));
                DrawString(_font,at+new Vector2(-7,5),S(country.Value),HorizontalAlignment.Left,-1,16,NationColor(country.Key));
            }
        }
        DrawString(_font,new Vector2(16,Size.Y-18),$"{_size} × {_size}     {_scale:0.0}×     滚轮缩放 · 右键 / 中键拖动 · 左键查看",HorizontalAlignment.Left,-1,13,new Color("bdc4ab"));
    }

    public static string S(JsonNode? node) => node?.ToString() ?? "";
    public static int I(JsonNode? node) => node?.GetValue<int>() ?? 0;
    public static double F(JsonNode? node) => node?.GetValue<double>() ?? 0;
}
