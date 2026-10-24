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
        set { _showCliffs = value; _cliffLayer?.QueueRedraw(); QueueRedraw(); }
    }
    public Font? LabelFont;
    public int DrawPasses { get; private set; }
    public int TerrainBuilds { get; private set; }
    public int TextureCount => _textures.Count;
    public int CliffCount => _cliffs.Count;
    public int SurfaceCount => _surfaces.Count;
    public int FrontFaceCount { get; private set; }
    // Shared wind clock; only root instances advance it, region nodes read it.
    public static float Now { get; private set; }
    private ImageTexture _ground = null!;
    private Texture2D _waterMask = null!;
    private static ImageTexture? _flatMask;
    private ShaderMaterial _waterMaterial = null!;
    private LayerNode _groundLayer = null!, _cliffLayer = null!, _objectLayer = null!;
    private Dictionary<string, Texture2D> _textures = new();
    private Dictionary<string, Texture2D?> _optional = new();
    // Structure id -> wind-clock start of its staged construction reveal;
    // shared from the root so per-region nodes animate from the same stamps.
    private Dictionary<string, float> _construction = new();
    private readonly List<((int X,int Y) Key,float Until)> _animating = new();
    private readonly Dictionary<(int,int),RegionArtwork> _regionNodes=new();
    private RegionPlan? _onlyRegion;
    private Rect2? _worldView;
    private string _occlusionKey="";
    private double _clock;
    public int RegionNodeCount=>_regionNodes.Count;

    // Optional drop-in art under assets/ (missing files keep the code visuals).
    private static string ArtSheet(string art) => art switch
    {
        var k when k.Contains("town-hall") => "actors/townhall",
        var k when k.Contains("farm") => "actors/farm",
        var k when k.Contains("mine") => "actors/mine",
        var k when k.Contains("oil-well") => "actors/oilwell",
        var k when k.Contains("factory") || k.Contains("power-plant") => "actors/factory",
        var k when k.Contains("barracks") => "actors/barracks",
        _ => ""
    };

    private const string WaterShaderCode = @"
shader_type canvas_item;
uniform sampler2D water_mask : filter_nearest, repeat_disable;
uniform vec2 mask_size = vec2(1152.0, 1152.0);
uniform float energy = 1.0;
float zh_hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
void fragment() {
    vec4 tex = texture(TEXTURE, UV);
    vec3 col = tex.rgb;
    float w = texture(water_mask, UV).r;
    if (w > 0.02) {
        vec2 px = UV * mask_size;
        float a = sin(px.x * 0.061 + px.y * 0.037 - TIME * 1.9);
        float b = sin(px.x * 0.023 - px.y * 0.047 + TIME * 1.24);
        float bands = (0.5 + 0.5 * a) * (0.5 + 0.5 * b);
        float sparkle = step(0.985, zh_hash(floor(px * 0.5) + floor(vec2(TIME * 1.6))));
        float shore = smoothstep(0.34, 0.10, w);
        float foam = 0.5 + 0.5 * sin(TIME * 1.5 + (px.x + px.y) * 0.08);
        col = mix(col, col * 0.90, w * 0.6);
        col += vec3(0.09, 0.15, 0.15) * bands * w * 0.55 * energy;
        col += vec3(0.42, 0.47, 0.47) * sparkle * w * energy;
        col = mix(col, vec3(0.86, 0.93, 0.89), shore * foam * 0.35);
    }
    COLOR = vec4(col, 1.0);
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
    private sealed record Surface(ImageTexture Ground, Texture2D Water, IReadOnlyList<CliffSegment> Cliffs);
    private readonly Dictionary<string, Surface> _surfaces = new();
    private int _detailLevel=2;
    public void SetDetailLevel(int level)
    {
        if(_detailLevel==level)return;
        _detailLevel=level;
        _groundLayer?.QueueRedraw();_cliffLayer?.QueueRedraw();_objectLayer?.QueueRedraw();
        foreach(var child in _regionNodes.Values)child.SetDetailLevel(level);
    }

    // Smooth land palette; band thresholds of the first pass became lerp stops.
    private static readonly (float H, string C)[] LandStops =
    {
        (0f, "90ad64"), (.18f, "7fa25a"), (.33f, "697f60"), (.48f, "89957a"),
        (.67f, "9da28b"), (.83f, "b1b19b"), (1f, "bcbca6")
    };
    private static Color TerrainColor(float h)
    {
        for (int i = 1; i < LandStops.Length; i++)
            if (h <= LandStops[i].H)
            {
                var (ha, ca) = LandStops[i - 1]; var (hb, cb) = LandStops[i];
                return new Color(ca).Lerp(new Color(cb), Math.Clamp((h - ha) / Math.Max(.0001f, hb - ha), 0, 1));
            }
        return new(LandStops[^1].C);
    }

    public void SetLayout(RegionLayout layout)
    {
        Layout = layout;
        EnsureLayers();
        string boundsKey=layout.GeometryKey+string.Join(";",layout.Regions.SelectMany(r=>r.Structures.Select(s=>$"{r.Input.X},{r.Input.Y},{s.Bounds}")));
        bool redrawGround=_occlusionKey!=boundsKey;_occlusionKey=boundsKey;
        if(layout.Input.LiveWorld)
            foreach(var r in layout.Regions)
            {
                var key=(r.Input.X,r.Input.Y);
                if(!_regionNodes.TryGetValue(key,out var node))
                {
                    node=new RegionArtwork { _textures=_textures,_optional=_optional,_construction=_construction,LabelFont=LabelFont,_detailLevel=_detailLevel };
                    _regionNodes[key]=node;AddChild(node);
                }
                node.Layout=layout;
                if(!ReferenceEquals(node._onlyRegion,r)){node._onlyRegion=r;node.QueueRedraw();}
                node.Visible=_worldView==null||_worldView.Value.Intersects(new Rect2(key.Item1*256,key.Item2*256,256,256));
            }
        if (_surfaces.TryGetValue(layout.GeometryKey, out var cached))
        {
            _ground = cached.Ground; _waterMask = cached.Water; _cliffs = cached.Cliffs;
            ApplySurface();
            if(redrawGround){_groundLayer.QueueRedraw();_cliffLayer.QueueRedraw();}
            return;
        }
        TerrainBuilds++;
        int samples=layout.Input.LiveWorld?48:128;
        float pixel=256f/samples;
        var image = Image.CreateEmpty(layout.Width * samples, layout.Height * samples, false, Image.Format.Rgb8);
        var mask = layout.Input.LiveWorld
            ? Image.CreateEmpty(layout.Width * samples, layout.Height * samples, false, Image.Format.R8)
            : null;
        for (int y = 0; y < image.GetHeight(); y++)
            for (int x = 0; x < image.GetWidth(); x++)
            {
                float wx = x * pixel, wy = y * pixel;
                float h = layout.Elevation(wx, wy);
                Color color = TerrainColor(h);
                float water = layout.Input.LiveWorld ? layout.Water(wx, wy) : 0;
                if (h > .12f)
                {
                    float dx = layout.Elevation(wx + 3, wy) - layout.Elevation(wx - 3, wy);
                    float dy = layout.Elevation(wx, wy + 3) - layout.Elevation(wx, wy - 3);
                    float shade = Math.Clamp((dx + dy) * 2.0f, -.24f, .20f);
                    // Faint rock strata follow the height field on steep faces.
                    float steep = Math.Abs(dx) + Math.Abs(dy);
                    if (steep > .10f && MathF.Abs((h * 20f) % 1f - .5f) < .09f) shade -= .05f;
                    color = shade > 0 ? color.Lightened(shade) : color.Darkened(-shade);
                }
                else
                {
                    float patch = layout.ValueNoise(wx / 44, wy / 44, 61);
                    color = color.Lerp(new Color("82955d"), patch * .35f);
                    color = color.Darkened(layout.WoodDensity(wx, wy) * .09f);
                }
                // Sparse grass tufts read as single chunky texels on purpose.
                if (h < .34f && water < .02f && layout.Hash(x, y, 201) % 83 < 2)
                    color = color.Lerp(new Color("a9c473"), .55f);
                if(layout.Input.LiveWorld)
                {
                    if (h < .25f && water < .02f)
                    {
                        float dry = layout.ValueNoise(wx / 150, wy / 150, 204);
                        if (dry > .72f) color = color.Lerp(new Color("a89f6d"), Math.Min(1, (dry - .72f) * 3f));
                    }
                    color=color.Lerp(new Color("b9ab77"),layout.Sand(wx,wy)*(1-h)*.85f);
                    color=color.Darkened(layout.WoodDensity(wx,wy)*.18f);
                    if(h>.72f)
                    {
                        color=color.Lerp(new Color("d5d9ce"),Math.Clamp((h-.72f)*2.8f,0,1));
                        if(layout.Hash(x,y,205)%47<3)color=color.Lightened(.07f);
                    }
                    if(water>0)
                    {
                        // Wet sand right at the moving waterline, pebbles below.
                        if(water>.03f&&water<.24f)color=color.Darkened(.08f);
                        if(water>.5f&&layout.Hash(x,y,203)%109<3)color=color.Lightened(.10f);
                        var sea=new Color("234f5a").Lerp(new Color("4c7d77"),1-water);
                        if(water<.5f)color=color.Lerp(new Color("b9b98c"),water*1.5f);
                        color=color.Lerp(sea,water);
                        mask!.SetPixel(x, y, new Color(water, 0, 0));
                    }
                }
                // Soft darkening along both cliff contour levels (baked AO).
                if (water < .02f && (MathF.Abs(h - .28f) < .012f || MathF.Abs(h - .52f) < .012f))
                    color = color.Darkened(.05f);
                // Fine grain is world-anchored, never resampled on camera moves.
                float grain = layout.Input.LiveWorld
                    ? ((int)(layout.Hash(x,y,13)%9)-4)*.003f*(1-water*.9f)
                    : ((int)(layout.Hash(x / 2, y / 2, 13) % 9) - 4) * .008f;
                image.SetPixel(x, y, grain > 0 ? color.Lightened(grain) : color.Darkened(-grain));
            }
        _ground = ImageTexture.CreateFromImage(image);
        image.Dispose();
        _waterMask = mask != null ? ImageTexture.CreateFromImage(mask) : FlatMask();
        mask?.Dispose();
        _cliffs = TerrainContours.Build(layout.Width * 256, layout.Height * 256, layout.Input.LiveWorld?16:8,
            new[] { .28f, .52f }, layout.Elevation);
        if (_surfaces.Count >= 2)
        {
            var oldest = _surfaces.Keys.First();
            var dropped = _surfaces[oldest];
            dropped.Ground.Dispose();
            if (dropped.Water != _flatMask && dropped.Water != _waterMask) dropped.Water.Dispose();
            _surfaces.Remove(oldest);
        }
        _surfaces[layout.GeometryKey] = new Surface(_ground, _waterMask, _cliffs);
        ApplySurface();
        _groundLayer.QueueRedraw(); _cliffLayer.QueueRedraw();
        QueueRedraw();
    }

    private static ImageTexture FlatMask()
    {
        if (_flatMask == null)
        {
            var image = Image.CreateEmpty(2, 2, false, Image.Format.R8);
            _flatMask = ImageTexture.CreateFromImage(image);
            image.Dispose();
        }
        return _flatMask;
    }

    private void ApplySurface()
    {
        _waterMaterial.SetShaderParameter("water_mask", _waterMask);
        _waterMaterial.SetShaderParameter("mask_size", new Vector2(_ground.GetWidth(), _ground.GetHeight()));
    }

    public override void _Process(double delta)
    {
        if (_onlyRegion != null) return;
        _clock += delta;
        if ((int)(_clock * 24) == (int)((_clock - delta) * 24)) return;
        Now = (float)_clock;
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

    private Texture2D Texture(string name)
    {
        if (!_textures.TryGetValue(name, out var texture)) _textures[name] = texture = GD.Load<Texture2D>("res://assets/" + name + ".png");
        return texture;
    }
    private Texture2D? OptionalTexture(string path)
    {
        if (_optional.TryGetValue(path, out var cached)) return cached;
        string full = "res://assets/" + path + ".png";
        return _optional[path] = ResourceLoader.Exists(full) ? GD.Load<Texture2D>(full) : null;
    }

    private static void Sprite(CanvasItem layer, Dictionary<string, Texture2D> textures, string name, Vector2 at, int column = 0, int row = 0, Color? tint = null)
    {
        // Retain one managed/native resource pair across draws. Re-loading a
        // transient wrapper per tree races finalization under sustained play.
        if(!textures.TryGetValue(name,out var texture))
            textures[name]=texture=GD.Load<Texture2D>("res://assets/"+name+".png");
        layer.DrawTextureRectRegion(texture,new Rect2(at,new Vector2(16,16)),new Rect2(column*16,row*16,16,16),tint);
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
            // Local footpaths join already accepted flat facility anchors.
            // They have no inter-region or rule movement meaning.
            for (int i = 1; i < r.Structures.Count; i++)
            {
                var a = r.Structures[i - 1].Bounds;
                var b = r.Structures[i].Bounds;
                var start = new Vector2(a.X + a.Width / 2, a.Y + a.Height);
                var end = new Vector2(b.X + b.Width / 2, b.Y + b.Height);
                // Clip paths against the same flat mask; never cut a road over a ridge.
                int steps = Math.Max(1, (int)start.DistanceTo(end));
                for (int s = 0; s <= steps; s++)
                {
                    var p = origin + start.Lerp(end, s / (float)steps);
                    if (Layout.Elevation(p.X, p.Y) < .18f) layer.DrawRect(new Rect2(p.Floor(), new Vector2(3, 2)), new Color("b0a575"));
                }
            }
            foreach (var rock in r.Rocks.Where((_,i)=>!Layout.Input.LiveWorld||_detailLevel>0||i%3==0))
                Sprite(layer,_textures,"rocks", origin + new Vector2(rock.X - 8, rock.Y - 16),
                    (int)(Layout.Hash((int)rock.X, (int)rock.Y, 71) % 3), tint: new Color(.80f, .86f, .72f));
            int outcropIndex = 0;
            foreach (var rock in r.Outcrops)
                layer.DrawTextureRect(Texture(outcropIndex++ % 2 == 0 ? "generated/mountain-outcrop-v1" : "generated/agy-mountain-cutout-v1"),
                    new Rect2(origin + new Vector2(rock.X, rock.Y), new Vector2(rock.Width, rock.Height)), false);
            foreach (var gold in r.Gold)
            {
                var p = origin + new Vector2(gold.X, gold.Y);
                layer.DrawColoredPolygon(new[] { p + new Vector2(-7, 0), p + new Vector2(-4, -6), p + new Vector2(4, -8), p + new Vector2(7, -1), p + new Vector2(1, 2) }, new Color("626a5c"));
                layer.DrawLine(p + new Vector2(-4, -3), p + new Vector2(1, -5), new Color("d6b459"), 2);
                layer.DrawLine(p + new Vector2(1, -5), p + new Vector2(4, -2), new Color("ead58a"), 1);
            }
            foreach (var building in r.Structures)
            {
                var b = building;
                objects.Add((origin.Y + b.Bounds.Y + b.Bounds.Height, () =>
                {
                    DrawStructure(layer,b,origin);
                    if(b.Count>1 && LabelFont!=null)layer.DrawString(LabelFont,origin+new Vector2(b.Bounds.X,b.Bounds.Y-3),$"×{b.Count}",fontSize:10,modulate:new Color("f4e3ac"));
                }));
            }
            foreach (var tree in r.Trees.Where((_,i)=>!Layout.Input.LiveWorld||i%(_detailLevel==0?6:_detailLevel==1?2:1)==0))
            {
                var t = tree;
                var p = origin + new Vector2(t.Foot.X, t.Foot.Y);
                float swayX = sway ? MathF.Sin(Now * 1.8f + t.Foot.X * .21f + t.Foot.Y * .13f) * 1.2f : 0;
                objects.Add((p.Y, () =>
                {
                    DrawEllipse(layer,p + new Vector2(-2, 0), new Vector2(7, 2), new Color(0, 0, 0, .12f));
                    string reserved = t.Sheet == "pine-trees" ? "actors/tree-pine" : "actors/tree-round";
                    var art = OptionalTexture(reserved);
                    if (art != null) layer.DrawTextureRect(art, new Rect2((p - new Vector2(8, 16)).Floor() + new Vector2(swayX, 0), new Vector2(16, 16)), false);
                    else Sprite(layer,_textures,t.Sheet, (p - new Vector2(8, 16)).Floor() + new Vector2(swayX, 0), t.Variant);
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

    private void DrawCliffFaces(CanvasItem layer)
    {
        FrontFaceCount = 0;
        if (Layout == null || !ShowCliffs) return;
        DrawPasses++;
        var buildings = Layout.Regions.SelectMany(r => r.Structures.Select(s => new Footprint(
            r.Input.X * 256 + s.Bounds.X, r.Input.Y * 256 + s.Bounds.Y, s.Bounds.Width, s.Bounds.Height))).ToArray();
        foreach (var cliff in _cliffs)
        {
            if (cliff.Depth == 0) continue;
            var a = new Vector2(cliff.A.X, cliff.A.Y); var b = new Vector2(cliff.B.X, cliff.B.Y);
            // Near-vertical projected faces have zero area at world-scale
            // float precision; the rasterizer must not triangulate those quads.
            if(Math.Abs(a.X-b.X)<.25f)continue;
            var bounds = new Footprint(Math.Min(a.X, b.X), Math.Min(a.Y, b.Y),
                Math.Abs(a.X - b.X) + 1, Math.Abs(a.Y - b.Y) + cliff.Depth);
            if (buildings.Any(f => f.Overlaps(bounds, 2))) continue;
            var down = new Vector2(0, cliff.Depth);
            var shade = cliff.Level > .5f ? new Color("76806a") : new Color("66705e");
            shade = shade.Darkened((Layout.Hash((int)a.X, (int)a.Y, 84) % 4) * .025f);
            layer.DrawColoredPolygon(new[] { a, b, b + down, a + down }, shade);
            // Horizontal strata inside tall faces; mossy caps and talus dots
            // on hash-picked segments keep repeated cliffs from reading alike.
            if (cliff.Depth >= 4)
            {
                var strata = shade.Darkened(.18f);
                layer.DrawLine(a + down * .45f, b + down * .45f, strata, 1);
                if (Layout.Hash((int)a.X, (int)a.Y, 85) % 2 == 0)
                    layer.DrawLine(a + down * .72f, b + down * .72f, strata.Darkened(.08f), 1);
            }
            layer.DrawLine(a + down, b + down, new Color("4f624d"), 1);
            if (Layout.Hash((int)a.X, (int)a.Y, 83) % 3 != 0)
                layer.DrawLine(a, b, new Color("8a9679"), 1);
            if (cliff.Depth >= 3 && Layout.Hash((int)a.X, (int)a.Y, 86) % 3 == 0)
                layer.DrawLine(a, b, new Color("8fa06b"), 1);
            layer.DrawLine((a + b) / 2, (a + b) / 2 + down, shade.Darkened(.24f), 1);
            if (cliff.Depth >= 3 && Layout.Hash((int)a.X, (int)a.Y, 87) % 4 == 0)
            {
                var talus = a.Lerp(b, .3f) + down;
                layer.DrawCircle(talus, 1.4f, shade.Darkened(.3f));
                layer.DrawCircle(a.Lerp(b, .62f) + down, 1.1f, shade.Darkened(.34f));
            }
            FrontFaceCount++;
        }
    }

    private static void DrawEllipse(CanvasItem layer, Vector2 center, Vector2 radius, Color color)
    {
        var points = Enumerable.Range(0, 16).Select(i => center + new Vector2(MathF.Cos(i * Mathf.Tau / 16) * radius.X, MathF.Sin(i * Mathf.Tau / 16) * radius.Y)).ToArray();
        layer.DrawColoredPolygon(points, color);
    }

    private void DrawStructure(CanvasItem layer, Structure s, Vector2 origin)
    {
        var p = (origin + new Vector2(s.Bounds.X, s.Bounds.Y)).Floor();
        var size = new Vector2(s.Bounds.Width, s.Bounds.Height);
        // Fresh sites rise in stages instead of popping in fully built.
        float t = 1;
        if (s.Remaining > 0 && _construction.TryGetValue(s.Id, out var start))
            t = Math.Clamp((Now - start) / 2.2f, 0, 1);
        if(Layout.Input.LiveWorld && t >= 1)
        {
            // Regular mountain terrain permits construction. Prefer low spots
            // and draw a local prepared terrace, without flattening every region
            // into a circular crater or rebuilding the entire height field.
            layer.DrawRect(new Rect2(p-new Vector2(2,1),size+new Vector2(4,3)),new Color("a39e70"));
        }
        if(s.Remaining>0)
        {
            var site=size;
            if(t<1)
            {
                // 0-.25 terrace clears and spreads, .15-.5 foundation fills from
                // the middle, .35-.75 corner posts rise, .65-1 braced frame set.
                var center=p+site*.5f;
                var spread=site+new Vector2(4,3);
                var ts=spread*(.55f+.45f*Math.Min(1,t*4));
                layer.DrawRect(new Rect2(center-ts*.5f,ts),new Color("a39e70",Math.Min(1,t*3)));
                if(t>.15f)
                {
                    float fill=Math.Clamp((t-.15f)/.35f,0,1);
                    var fs=site*(.3f+.7f*fill);
                    layer.DrawRect(new Rect2(center-fs*.5f,fs),new Color("766a49"));
                }
                if(t>.35f)
                {
                    float posts=Math.Clamp((t-.35f)/.4f,0,1);
                    for(int x=0;x<=site.X;x+=7)
                        layer.DrawLine(p+new Vector2(x,site.Y),p+new Vector2(x,site.Y*(1-posts)),new Color("725737",Math.Min(1,posts*2)),2);
                }
                if(t>.65f)
                {
                    float brace=Math.Clamp((t-.65f)/.35f,0,1);
                    layer.DrawRect(new Rect2(p+new Vector2(2,4),site-new Vector2(4,5)),new Color("c3b07b",brace),false,2);
                    layer.DrawLine(p,p+site,new Color("d6bc7b",brace),1);
                    layer.DrawLine(p+new Vector2(site.X,0),p+new Vector2(0,site.Y),new Color("725737",brace),1);
                }
                return;
            }
            layer.DrawRect(new Rect2(p,site),new Color("766a49"));
            layer.DrawRect(new Rect2(p+new Vector2(2,4),site-new Vector2(4,5)),new Color("c3b07b"),false,2);
            for(int x=0;x<=site.X;x+=7)layer.DrawLine(p+new Vector2(x,0),p+new Vector2(x,site.Y),new Color("725737"),2);
            layer.DrawLine(p,p+site,new Color("d6bc7b"),1);
            layer.DrawLine(p+new Vector2(site.X,0),p+new Vector2(0,site.Y),new Color("725737"),1);return;
        }
        // Reserved Gemini art wins over the stock sheets when the file exists.
        string sheet = ArtSheet(s.Art);
        var art = sheet == "" ? null : OptionalTexture(sheet);
        if (art != null)
        {
            var pixels=art.GetSize();
            var fitted=pixels*Math.Min(size.X/pixels.X,size.Y/pixels.Y);
            layer.DrawTextureRect(art,new Rect2(p+new Vector2((size.X-fitted.X)*.5f,size.Y-fitted.Y),fitted),false);
            return;
        }
        if (s.Kind == "farm")
        {
            layer.DrawRect(new Rect2(p, new Vector2(24, 18)), new Color("786940"));
            for (int row = 0; row < 4; row++)
            {
                layer.DrawLine(p + new Vector2(2, 3 + row * 4), p + new Vector2(22, 3 + row * 4), new Color("b4a25a"), 2);
                for (int x = 0; x < 6; x++) layer.DrawRect(new Rect2(p + new Vector2(3 + x * 3, 1 + row * 4), new Vector2(1, 3)), new Color("dbca71"));
            }
        }
        else if (s.Kind == "mine")
        {
            layer.DrawRect(new Rect2(p, new Vector2(22, 16)), new Color("8e8968"));
            Sprite(layer,_textures,"mineshafts", p);
            layer.DrawRect(new Rect2(p + new Vector2(16, 10), new Vector2(5, 4)), new Color("b9a477"));
        }
        else
        {
            Sprite(layer,_textures,"houses", p, (int)(Layout.Hash((int)p.X, (int)p.Y, 9) % 3), 2);
            // Door and a warm window; the overlay adds the flicker on top.
            layer.DrawRect(new Rect2(p + new Vector2(7, 11), new Vector2(2, 4)), new Color("4b3a26"));
            layer.DrawRect(new Rect2(p + new Vector2(4, 6), new Vector2(2, 2)), new Color("e8c06a"));
        }
    }

    private static void DrawPerson(CanvasItem layer, Vector2 at)
    {
        var p = (at - new Vector2(3, 8)).Floor();
        // Original 6x8 code-drawn scale figures, pending the character art pass.
        layer.DrawRect(new Rect2(p + new Vector2(1, 7), new Vector2(6, 2)), new Color(0, 0, 0, .16f));
        layer.DrawRect(new Rect2(p + new Vector2(1, 1), new Vector2(4, 2)), new Color("ddbc8c"));
        layer.DrawRect(new Rect2(p + new Vector2(0, 0), new Vector2(6, 1)), new Color("645541"));
        layer.DrawRect(new Rect2(p + new Vector2(1, 3), new Vector2(4, 3)), new Color("587c84"));
        layer.DrawRect(new Rect2(p + new Vector2(1, 6), new Vector2(1, 2)), new Color("454f46"));
        layer.DrawRect(new Rect2(p + new Vector2(4, 6), new Vector2(1, 2)), new Color("454f46"));
    }

}
