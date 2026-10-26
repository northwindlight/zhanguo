using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using Zhanguo.Desktop.Layout;

// Retained static artwork. Camera transforms belong to the clipping parent.
// The root draws through three ordered child layers (ground, cliffs, objects)
// so the water shader only ever covers the textured ground pass, and live
// per-region nodes keep drawing above them in child order. CanvasItem draw
// calls must target the node whose _Draw is running, so every helper takes
// the drawing layer as its receiver.
public partial class RegionArtwork : Node2D
{
    public RegionLayout Layout { get; private set; } = null!;
    private bool _showCliffs = true;
    public bool ShowCliffs
    {
        get => _showCliffs;
        set { _showCliffs = value; _waterMaterial?.SetShaderParameter("show_cliffs",value); _groundLayer?.QueueRedraw(); }
    }
    public Font? LabelFont;
    public void SetLabelFont(Font font)
    {
        LabelFont=font;QueueRedraw();
        foreach(var node in _regionNodes.Values){node.LabelFont=font;node.QueueRedraw();}
    }
    public int DrawPasses { get; private set; }
    public int TerrainBuilds { get; private set; }
    public int TextureCount => 1; // shared original atlas; no per-tree textures
    public int CliffCount => FrontFaceCount;
    public int SurfaceCount => _surfaces.Count;
    public int FrontFaceCount { get; private set; }
    // Shared wind clock; only root instances advance it, region nodes read it.
    public static float Now { get; private set; }
    private ImageTexture _ground = null!;
    public int PixelMountainCells { get; private set; }
    public int PixelWaterCells { get; private set; }
    private Texture2D _waterMask = null!;
    private ShaderMaterial _waterMaterial = null!;
    private LayerNode _groundLayer = null!, _cliffLayer = null!, _objectLayer = null!;
    // Structure id -> wind-clock start of its staged construction reveal;
    // shared from the root so per-region nodes animate from the same stamps.
    private Dictionary<string, float> _construction = new();
    private readonly List<((int X,int Y) Key,float Until)> _animating = new();
    private readonly Dictionary<(int,int),RegionArtwork> _regionNodes=new();
    private RegionPlan? _onlyRegion;
    private Rect2? _worldView;
    private double _clock;
    public int RegionNodeCount=>_regionNodes.Count;

    private const string WaterShaderCode = @"
shader_type canvas_item;
uniform sampler2D water_mask : filter_nearest, repeat_disable;
uniform sampler2D art_atlas : filter_nearest, repeat_disable;
uniform vec2 mask_size = vec2(768.0);
uniform vec4 deep_rect;
uniform vec4 mid_rect;
uniform vec4 shallow_rect;
uniform float clock = 0.0;
uniform bool show_cliffs = true;
void fragment() {
    vec4 tex = texture(TEXTURE, UV);
    float water = texture(water_mask, UV).r;
    if (water > 0.1) {
        vec2 px = floor(UV * mask_size);
        vec2 tile = floor(px / 16.0);
        float phase = mod(floor(clock * 4.0) + mod(tile.x * 3.0 + tile.y, 6.0), 6.0);
        vec4 r = water > 0.8 ? deep_rect : water > 0.5 ? mid_rect : shallow_rect;
        vec2 at = r.xy + vec2(phase * 16.0, 0.0) + mod(px, 16.0) + vec2(0.5);
        tex = texture(art_atlas, at / vec2(1024.0));
    }
    if (!show_cliffs && texture(water_mask, UV).g > 0.5) tex = vec4(0.44, 0.48, 0.45, 1.0);
    COLOR = tex;
}
";

    private void EnsureLayers()
    {
        if (_groundLayer != null) return;
        _waterMaterial = new ShaderMaterial { Shader = new Shader { Code = WaterShaderCode } };
        _groundLayer = new LayerNode(this, Layer.Ground) { Material = _waterMaterial };
        _cliffLayer = new LayerNode(this, Layer.Cliffs);
        _objectLayer = new LayerNode(this, Layer.Objects);
        AddChild(_groundLayer); AddChild(_cliffLayer); AddChild(_objectLayer);
    }

    private enum Layer { Ground, Cliffs, Objects }
    private sealed partial class LayerNode : Node2D
    {
        private readonly RegionArtwork _owner; private readonly Layer _layer;
        public LayerNode(RegionArtwork owner, Layer layer) { _owner = owner; _layer = layer; }
        public override void _Draw()
        {
            switch (_layer)
            {
                case Layer.Ground: _owner.DrawGround(this); break;
                case Layer.Cliffs: _owner.DrawCliffFaces(this); break;
                default: _owner.DrawSampleObjects(this); break;
            }
        }
    }

    public void SetWorldView(Rect2 bounds)
    {
        _worldView=bounds.Grow(40);
        foreach(var (at,node) in _regionNodes)node.Visible=_worldView.Value.Intersects(new Rect2(at.Item1*256,at.Item2*256,256,256));
    }
    private IReadOnlyList<CliffSegment> _cliffs = Array.Empty<CliffSegment>();
    private sealed record Surface(ImageTexture Ground, Texture2D Water, IReadOnlyList<CliffSegment> Cliffs,int Mountains,int Waters,int Faces);
    private readonly Dictionary<string, Surface> _surfaces = new();
    private int _detailLevel=2;
    private IReadOnlyList<Point>? _roads;
    public void SetDetailLevel(int level)
    {
        if(_detailLevel==level)return;
        _detailLevel=level;
        _groundLayer?.QueueRedraw();_cliffLayer?.QueueRedraw();_objectLayer?.QueueRedraw();
        foreach(var child in _regionNodes.Values)child.SetDetailLevel(level);
    }

    public void SetLayout(RegionLayout layout)
    {
        Layout = layout;
        EnsureLayers();
        if(layout.Input.LiveWorld)
            foreach(var r in layout.Regions)
            {
                var key=(r.Input.X,r.Input.Y);
                if(!_regionNodes.TryGetValue(key,out var node))
                {
                    node=new RegionArtwork { _construction=_construction,LabelFont=LabelFont,_detailLevel=_detailLevel };
                    _regionNodes[key]=node;AddChild(node);
                }
                node.Layout=layout;
                var roads=layout.Roads.For(r);
                if(!ReferenceEquals(node._onlyRegion,r)||!ReferenceEquals(node._roads,roads))
                {node._onlyRegion=r;node._roads=roads;node.QueueRedraw();}
                node.Visible=_worldView==null||_worldView.Value.Intersects(new Rect2(key.Item1*256,key.Item2*256,256,256));
            }
        if (_surfaces.TryGetValue(layout.GeometryKey, out var cached))
        {
            _ground = cached.Ground; _waterMask = cached.Water; _cliffs = cached.Cliffs;
            ApplySurface();
            PixelMountainCells=cached.Mountains;PixelWaterCells=cached.Waters;FrontFaceCount=cached.Faces;
            _groundLayer.QueueRedraw();_cliffLayer.QueueRedraw();
            return;
        }
        TerrainBuilds++;
        BakePixelSurface(layout);
        if (_surfaces.Count >= 2)
        {
            var oldest = _surfaces.Keys.First();
            var dropped = _surfaces[oldest];
            dropped.Ground.Dispose();
            dropped.Water.Dispose();
            _surfaces.Remove(oldest);
        }
        _surfaces[layout.GeometryKey] = new Surface(_ground, _waterMask, _cliffs,PixelMountainCells,PixelWaterCells,FrontFaceCount);
        ApplySurface();
        _groundLayer.QueueRedraw(); _cliffLayer.QueueRedraw();
        QueueRedraw();
    }

    private void ApplySurface()
    {
        _waterMaterial.SetShaderParameter("water_mask", _waterMask);
        _waterMaterial.SetShaderParameter("show_cliffs",_showCliffs);
        _waterMaterial.SetShaderParameter("mask_size", new Vector2(_ground.GetWidth(), _ground.GetHeight()));
        _waterMaterial.SetShaderParameter("art_atlas",UnifiedArt.Atlas);
        foreach(var pair in new[]{("deep","deep_rect"),("mid","mid_rect"),("shallow","shallow_rect")})
        {
            var r=UnifiedArt.Get("water-"+pair.Item1).Rect;
            _waterMaterial.SetShaderParameter(pair.Item2,new Vector4(r.Position.X,r.Position.Y,r.Size.X,r.Size.Y));
        }
    }

    public override void _Process(double delta)
    {
        if (_onlyRegion != null) return;
        _clock += delta;
        if ((int)(_clock * 24) == (int)((_clock - delta) * 24)) return;
        Now = (float)_clock;
        _waterMaterial?.SetShaderParameter("clock",Now);
        // Staged construction replays redraw their region at any zoom level.
        for (int i = _animating.Count - 1; i >= 0; i--)
        {
            if (Now > _animating[i].Until) { _animating.RemoveAt(i); continue; }
            if (_regionNodes.TryGetValue(_animating[i].Key, out var node)) node.QueueRedraw();
        }
        if (_detailLevel < 2) return;
        _objectLayer?.QueueRedraw();
        foreach (var node in _regionNodes.Values)
            if (node.Visible) node.QueueRedraw();
    }

    // WorldMap calls this right after a frame introduces a construction site.
    public void BeginConstruction(string id)
    {
        if (Layout == null) return;
        _construction[id] = Now;
        foreach (var r in Layout.Regions)
            if (r.Structures.Any(s => s.Id == id))
            {
                var key = (r.Input.X, r.Input.Y);
                if (_regionNodes.ContainsKey(key)) _animating.Add((key, Now + 2.6f));
                return;
            }
    }

    public override void _Draw()
    {
        // Only live per-region nodes draw through this entry; the root delegates
        // to its layers so the water shader cannot leak onto cliffs or objects.
        if (_onlyRegion == null) return;
        DrawPasses++;
        DrawRegionObjects(this, _onlyRegion);
    }

    private void DrawGround(CanvasItem layer)
    {
        if (Layout == null) return;
        DrawPasses++;
        layer.DrawTextureRect(_ground, new Rect2(0, 0, Layout.Width * 256, Layout.Height * 256), false);
    }

    private void DrawSampleObjects(CanvasItem layer)
    {
        if (Layout == null) return;
        if (Layout.Input.LiveWorld) return;
        DrawPasses++;
        DrawRegionObjects(layer, null);
    }

    private void DrawRegionObjects(CanvasItem layer, RegionPlan? only)
    {
        var objects = new List<(float Y, Action Draw)>();
        bool sway = _detailLevel == 2;
        foreach (var r in only==null?Layout!.Regions:new[]{only})
        {
            var origin = new Vector2(r.Input.X * 256, r.Input.Y * 256);
            DrawRoads(layer,r,origin);
            DrawTownGround(layer,r,origin);
            DrawOilPatches(layer,r,origin);
            foreach(var rock in r.Rocks.Where((_,i)=>!Layout.Input.LiveWorld||_detailLevel>0||i%3==0))
                UnifiedArt.Draw(layer,"boulder-"+(Layout.Hash((int)rock.X,(int)rock.Y,71)%2),DepositFoot(origin,rock));
            // Mountain mass is formed from connected 16px terrain cells.
            // The old large cutout boulders no longer cover the mountain caps.
            foreach(var gold in r.Gold)
                UnifiedArt.Draw(layer,"ore-gold-"+(Layout.Hash((int)gold.X,(int)gold.Y,72)%2),DepositFoot(origin,gold));
            foreach(var ore in r.Iron)
                UnifiedArt.Draw(layer,"ore-iron-"+(Layout.Hash((int)ore.X,(int)ore.Y,306)%2),DepositFoot(origin,ore));
            foreach (var building in r.Structures)
            {
                var b = building;
                objects.Add((origin.Y + b.Bounds.Y + b.Bounds.Height, () =>
                {
                    DrawStructure(layer,b,origin);
                    if(b.Count>1 && LabelFont!=null)layer.DrawString(LabelFont,origin+new Vector2(b.Bounds.X,b.Bounds.Y-3),$"×{b.Count}",fontSize:16,modulate:new Color("f4e3ac"));
                }));
            }
            if(_detailLevel>0||!Layout.Input.LiveWorld)
                foreach(var lot in r.TownLots)
                {
                    var decoration=lot;
                    objects.Add((origin.Y+lot.Bounds.Y+lot.Bounds.Height,()=>DrawTownLot(layer,decoration,origin)));
                }
            foreach (var tree in r.Trees.Where((_,i)=>!Layout.Input.LiveWorld||i%(_detailLevel==0?6:_detailLevel==1?2:1)==0))
            {
                var t = tree;
                var p = origin + new Vector2(t.Foot.X, t.Foot.Y);
                objects.Add((p.Y, () =>
                {
                    if(t.Sheet=="pine-trees")UnifiedArt.Draw(layer,"tree-pine",p);
                    else
                    {
                        int frame=sway?(int)(Now*6+t.Foot.X*.17f+t.Foot.Y*.11f)%8:0;
                        UnifiedArt.Draw(layer,"tree-oak-sway",p,frame);
                    }
                }));
            }
            foreach (var person in r.People.Where(_=>!Layout.Input.LiveWorld))
            {
                var p = origin + new Vector2(person.X, person.Y);
                objects.Add((p.Y, () => DrawPerson(layer,p)));
            }
        }
        foreach (var item in objects.OrderBy(o => o.Y)) item.Draw();
    }

    private Vector2 DepositFoot(Vector2 origin,Point point)
    {
        var pointOnCap=Layout.DepositPoint(new Point(origin.X+point.X,origin.Y+point.Y));
        return new Vector2(pointOnCap.X,pointOnCap.Y);
    }

    private void DrawCliffFaces(CanvasItem layer) { }

    private static void DrawEllipse(CanvasItem layer, Vector2 center, Vector2 radius, Color color)
    {
        var points = Enumerable.Range(0, 16).Select(i => center + new Vector2(MathF.Cos(i * Mathf.Tau / 16) * radius.X, MathF.Sin(i * Mathf.Tau / 16) * radius.Y)).ToArray();
        layer.DrawColoredPolygon(points, color);
    }

    private void DrawStructure(CanvasItem layer,Structure s,Vector2 origin)
    {
        var p=origin+new Vector2(s.Bounds.X,s.Bounds.Y);
        var size=new Vector2(s.Bounds.Width,s.Bounds.Height);
        string id=UnifiedArt.Building(s.Art,s.Kind);
        var e=UnifiedArt.Get(id);
        // Every map uses exactly one world pixel per source pixel.
        const float scale=1;
        var foot=p+new Vector2(size.X*.5f,size.Y);
        if(s.Remaining>0)
        {
            float t=_construction.TryGetValue(s.Id,out var start)?Math.Clamp((Now-start)/2.2f,0,1):1;
            var rect=new Rect2(p,size);layer.DrawRect(rect,new Color("8f805d"));
            var c=UnifiedArt.Get("construction");
            var at=foot-c.Anchor*scale;
            int visible=Math.Max(1,(int)(c.Cell.Y*Math.Clamp(t*1.5f,.18f,1)));
            layer.DrawTextureRectRegion(UnifiedArt.Atlas,new Rect2(at+new Vector2(0,c.Cell.Y-visible)*scale,new Vector2(c.Cell.X,visible)*scale),
                new Rect2(c.Rect.Position+new Vector2I(0,c.Cell.Y-visible),new Vector2I(c.Cell.X,visible)));
            return;
        }
        UnifiedArt.Draw(layer,id,foot,scale:scale);
        if(id=="townhall")DrawTownHallDetails(layer,s,p,size);
        if(_detailLevel==2&&e.Effect is Vector2 effect)
        {
            var chimney=foot-e.Anchor*scale+effect*scale;
            for(int i=0;i<3;i++)
            {
                float t=(Now*.42f+i/3f)%1;
                var at=(chimney+new Vector2(MathF.Sin(t*4+i)*3,-t*18)*scale).Round();
                layer.DrawRect(new Rect2(at,Vector2.One*(2+(int)(t*3))*scale),new Color(.74f,.76f,.68f,(1-t)*.45f));
            }
        }
    }

    private static void DrawPerson(CanvasItem layer, Vector2 at)
    {
        UnifiedArt.Draw(layer,"actor-resident",at);
    }

}
