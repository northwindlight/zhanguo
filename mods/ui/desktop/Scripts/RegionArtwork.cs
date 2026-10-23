using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using Zhanguo.Desktop.Layout;

// Retained static artwork. Camera transforms belong to the clipping parent.
public partial class RegionArtwork : Node2D
{
    public RegionLayout Layout { get; private set; } = null!;
    public bool ShowCliffs = true;
    public Font? LabelFont;
    public int DrawPasses { get; private set; }
    public int TerrainBuilds { get; private set; }
    public int TextureCount => _textures.Count;
    public int CliffCount => _cliffs.Count;
    public int SurfaceCount => _surfaces.Count;
    public int FrontFaceCount { get; private set; }
    private ImageTexture _ground = null!;
    private Dictionary<string, Texture2D> _textures = new();
    private readonly Dictionary<(int,int),RegionArtwork> _regionNodes=new();
    private RegionPlan? _onlyRegion;
    private Rect2? _worldView;
    private string _occlusionKey="";
    public int RegionNodeCount=>_regionNodes.Count;
    public void SetWorldView(Rect2 bounds)
    {
        _worldView=bounds.Grow(40);
        foreach(var (at,node) in _regionNodes)node.Visible=_worldView.Value.Intersects(new Rect2(at.Item1*256,at.Item2*256,256,256));
    }
    private IReadOnlyList<CliffSegment> _cliffs = Array.Empty<CliffSegment>();
    private sealed record Surface(ImageTexture Ground, IReadOnlyList<CliffSegment> Cliffs);
    private readonly Dictionary<string, Surface> _surfaces = new();
    private int _detailLevel=2;
    public void SetDetailLevel(int level)
    {
        if(_detailLevel==level)return;
        _detailLevel=level;QueueRedraw();
        foreach(var child in _regionNodes.Values)child.SetDetailLevel(level);
    }

    public void SetLayout(RegionLayout layout)
    {
        Layout = layout;
        string boundsKey=layout.GeometryKey+string.Join(";",layout.Regions.SelectMany(r=>r.Structures.Select(s=>$"{r.Input.X},{r.Input.Y},{s.Bounds}")));
        bool redrawGround=_occlusionKey!=boundsKey;_occlusionKey=boundsKey;
        if(layout.Input.LiveWorld)
            foreach(var r in layout.Regions)
            {
                var key=(r.Input.X,r.Input.Y);
                if(!_regionNodes.TryGetValue(key,out var node))
                {
                    node=new RegionArtwork { _textures=_textures,LabelFont=LabelFont,_detailLevel=_detailLevel };
                    _regionNodes[key]=node;AddChild(node);
                }
                node.Layout=layout;
                if(!ReferenceEquals(node._onlyRegion,r)){node._onlyRegion=r;node.QueueRedraw();}
                node.Visible=_worldView==null||_worldView.Value.Intersects(new Rect2(key.Item1*256,key.Item2*256,256,256));
            }
        if (_surfaces.TryGetValue(layout.GeometryKey, out var cached))
        {
            _ground = cached.Ground; _cliffs = cached.Cliffs;if(redrawGround)QueueRedraw();return;
        }
        TerrainBuilds++;
        int samples=layout.Input.LiveWorld?48:128;
        float pixel=256f/samples;
        var image = Image.CreateEmpty(layout.Width * samples, layout.Height * samples, false, Image.Format.Rgb8);
        for (int y = 0; y < image.GetHeight(); y++)
            for (int x = 0; x < image.GetWidth(); x++)
            {
                float wx = x * pixel, wy = y * pixel;
                float h = layout.Elevation(wx, wy);
                Color color = h switch
                {
                    > .83f => new("b1b19b"), > .67f => new("9da28b"),
                    > .48f => new("89957a"), > .33f => new("74866b"),
                    > .18f => new("697f60"), > .09f => new("829a5c"), _ => new("92ab66")
                };
                if (h > .12f)
                {
                    float dx = layout.Elevation(wx + 3, wy) - layout.Elevation(wx - 3, wy);
                    float dy = layout.Elevation(wx, wy + 3) - layout.Elevation(wx, wy - 3);
                    float shade = Math.Clamp((dx + dy) * 2.0f, -.24f, .20f);
                    color = shade > 0 ? color.Lightened(shade) : color.Darkened(-shade);
                }
                else
                {
                    float patch = layout.ValueNoise(wx / 44, wy / 44, 61);
                    color = color.Lerp(new Color("82955d"), patch * .35f);
                    color = color.Darkened(layout.WoodDensity(wx, wy) * .09f);
                }
                if(layout.Input.LiveWorld)
                {
                    color=color.Lerp(new Color("b9ab77"),layout.Sand(wx,wy)*(1-h)*.85f);
                    color=color.Darkened(layout.WoodDensity(wx,wy)*.18f);
                    if(h>.72f)color=color.Lerp(new Color("d5d9ce"),Math.Clamp((h-.72f)*2.8f,0,1));
                    float water=layout.Water(wx,wy);
                    if(water>0)
                    {
                        var sea=new Color("234f5a").Lerp(new Color("4c7d77"),1-water);
                        if(water<.5f)color=color.Lerp(new Color("b9b98c"),water*1.5f);
                        color=color.Lerp(sea,water);
                    }
                }
                // Fine grain is world-anchored, never resampled on camera moves.
                float grain = layout.Input.LiveWorld
                    ? ((int)(layout.Hash(x,y,13)%9)-4)*.003f*(1-layout.Water(wx,wy)*.9f)
                    : ((int)(layout.Hash(x / 2, y / 2, 13) % 9) - 4) * .008f;
                image.SetPixel(x, y, grain > 0 ? color.Lightened(grain) : color.Darkened(-grain));
            }
        _ground = ImageTexture.CreateFromImage(image);
        image.Dispose();
        _cliffs = TerrainContours.Build(layout.Width * 256, layout.Height * 256, layout.Input.LiveWorld?16:8,
            new[] { .28f, .52f }, layout.Elevation);
        if (_surfaces.Count >= 2)
        {
            var oldest = _surfaces.Keys.First();
            _surfaces[oldest].Ground.Dispose(); _surfaces.Remove(oldest);
        }
        _surfaces[layout.GeometryKey] = new Surface(_ground, _cliffs);
        QueueRedraw();
    }

    private Texture2D Texture(string name)
    {
        if (!_textures.TryGetValue(name, out var texture)) _textures[name] = texture = GD.Load<Texture2D>("res://assets/" + name + ".png");
        return texture;
    }

    private void Sprite(string name, Vector2 at, int column = 0, int row = 0, Color? tint = null)
        => DrawTextureRectRegion(Texture(name), new Rect2(at, new Vector2(16, 16)), new Rect2(column * 16, row * 16, 16, 16), tint);

    public override void _Draw()
    {
        if (Layout == null) return;
        DrawPasses++;
        if(_onlyRegion==null)
        {
            DrawTextureRect(_ground, new Rect2(0, 0, Layout.Width * 256, Layout.Height * 256), false);
            DrawCliffs();
            if(Layout.Input.LiveWorld)return;
        }
        var objects = new List<(float Y, Action Draw)>();
        foreach (var r in _onlyRegion==null?Layout.Regions:new[]{_onlyRegion})
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
                    if (Layout.Elevation(p.X, p.Y) < .18f) DrawRect(new Rect2(p.Floor(), new Vector2(3, 2)), new Color("b0a575"));
                }
            }
            foreach (var rock in r.Rocks.Where((_,i)=>!Layout.Input.LiveWorld||_detailLevel>0||i%3==0))
                Sprite("rocks", origin + new Vector2(rock.X - 8, rock.Y - 16),
                    (int)(Layout.Hash((int)rock.X, (int)rock.Y, 71) % 3), tint: new Color(.80f, .86f, .72f));
            int outcropIndex = 0;
            foreach (var rock in r.Outcrops)
                DrawTextureRect(Texture(outcropIndex++ % 2 == 0 ? "generated/mountain-outcrop-v1" : "generated/agy-mountain-cutout-v1"),
                    new Rect2(origin + new Vector2(rock.X, rock.Y), new Vector2(rock.Width, rock.Height)), false);
            foreach (var gold in r.Gold)
            {
                var p = origin + new Vector2(gold.X, gold.Y);
                DrawColoredPolygon(new[] { p + new Vector2(-7, 0), p + new Vector2(-4, -6), p + new Vector2(4, -8), p + new Vector2(7, -1), p + new Vector2(1, 2) }, new Color("626a5c"));
                DrawLine(p + new Vector2(-4, -3), p + new Vector2(1, -5), new Color("d6b459"), 2);
                DrawLine(p + new Vector2(1, -5), p + new Vector2(4, -2), new Color("ead58a"), 1);
            }
            foreach (var building in r.Structures)
            {
                var b = building;
                objects.Add((origin.Y + b.Bounds.Y + b.Bounds.Height, () =>
                {
                    DrawStructure(b,origin);
                    if(b.Count>1 && LabelFont!=null)DrawString(LabelFont,origin+new Vector2(b.Bounds.X,b.Bounds.Y-3),$"×{b.Count}",fontSize:10,modulate:new Color("f4e3ac"));
                }));
            }
            foreach (var tree in r.Trees.Where((_,i)=>!Layout.Input.LiveWorld||i%(_detailLevel==0?6:_detailLevel==1?2:1)==0))
            {
                var t = tree;
                var p = origin + new Vector2(t.Foot.X, t.Foot.Y);
                objects.Add((p.Y, () =>
                {
                    DrawEllipse(p + new Vector2(-2, 0), new Vector2(7, 2), new Color(0, 0, 0, .12f));
                    Sprite(t.Sheet, (p - new Vector2(8, 16)).Floor(), t.Variant);
                }));
            }
            foreach (var person in r.People.Where(_=>!Layout.Input.LiveWorld))
            {
                var p = origin + new Vector2(person.X, person.Y);
                objects.Add((p.Y, () => DrawPerson(p)));
            }
        }
        foreach (var item in objects.OrderBy(o => o.Y)) item.Draw();
    }

    private void DrawCliffs()
    {
        FrontFaceCount = 0;
        if (!ShowCliffs) return;
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
            DrawColoredPolygon(new[] { a, b, b + down, a + down }, shade);
            DrawLine(a + down, b + down, new Color("4f624d"), 1);
            if (Layout.Hash((int)a.X, (int)a.Y, 83) % 3 != 0)
                DrawLine(a, b, new Color("8a9679"), 1);
            DrawLine((a + b) / 2, (a + b) / 2 + down, shade.Darkened(.24f), 1);
            FrontFaceCount++;
        }
    }

    private void DrawEllipse(Vector2 center, Vector2 radius, Color color)
    {
        var points = Enumerable.Range(0, 16).Select(i => center + new Vector2(MathF.Cos(i * Mathf.Tau / 16) * radius.X, MathF.Sin(i * Mathf.Tau / 16) * radius.Y)).ToArray();
        DrawColoredPolygon(points, color);
    }

    private void DrawStructure(Structure s, Vector2 origin)
    {
        var p = (origin + new Vector2(s.Bounds.X, s.Bounds.Y)).Floor();
        if(Layout.Input.LiveWorld)
        {
            // Regular mountain terrain permits construction. Prefer low spots
            // and draw a local prepared terrace, without flattening every region
            // into a circular crater or rebuilding the entire height field.
            DrawRect(new Rect2(p-new Vector2(2,1),new Vector2(s.Bounds.Width+4,s.Bounds.Height+3)),new Color("a39e70"));
        }
        if(s.Remaining>0)
        {
            var size=new Vector2(s.Bounds.Width,s.Bounds.Height);
            DrawRect(new Rect2(p,size),new Color("766a49"));
            DrawRect(new Rect2(p+new Vector2(2,4),size-new Vector2(4,5)),new Color("c3b07b"),false,2);
            for(int x=0;x<=s.Bounds.Width;x+=7)DrawLine(p+new Vector2(x,0),p+new Vector2(x,s.Bounds.Height),new Color("725737"),2);
            DrawLine(p,p+size,new Color("d6bc7b"),1);return;
        }
        if (s.Kind == "farm")
        {
            DrawRect(new Rect2(p, new Vector2(24, 18)), new Color("786940"));
            for (int row = 0; row < 4; row++)
            {
                DrawLine(p + new Vector2(2, 3 + row * 4), p + new Vector2(22, 3 + row * 4), new Color("b4a25a"), 2);
                for (int x = 0; x < 6; x++) DrawRect(new Rect2(p + new Vector2(3 + x * 3, 1 + row * 4), new Vector2(1, 3)), new Color("dbca71"));
            }
        }
        else if (s.Kind == "mine")
        {
            DrawRect(new Rect2(p, new Vector2(22, 16)), new Color("8e8968"));
            Sprite("mineshafts", p);
            DrawRect(new Rect2(p + new Vector2(16, 10), new Vector2(5, 4)), new Color("b9a477"));
        }
        else Sprite("houses", p, (int)(Layout.Hash((int)p.X, (int)p.Y, 9) % 3), 2);
    }

    private void DrawPerson(Vector2 at)
    {
        var p = (at - new Vector2(3, 8)).Floor();
        // Original 6x8 code-drawn scale figures, pending the character art pass.
        DrawRect(new Rect2(p + new Vector2(1, 7), new Vector2(6, 2)), new Color(0, 0, 0, .16f));
        DrawRect(new Rect2(p + new Vector2(1, 1), new Vector2(4, 2)), new Color("ddbc8c"));
        DrawRect(new Rect2(p + new Vector2(0, 0), new Vector2(6, 1)), new Color("645541"));
        DrawRect(new Rect2(p + new Vector2(1, 3), new Vector2(4, 3)), new Color("587c84"));
        DrawRect(new Rect2(p + new Vector2(1, 6), new Vector2(1, 2)), new Color("454f46"));
        DrawRect(new Rect2(p + new Vector2(4, 6), new Vector2(1, 2)), new Color("454f46"));
    }

}
