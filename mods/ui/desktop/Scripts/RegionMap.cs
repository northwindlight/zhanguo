using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using Zhanguo.Desktop.Layout;

// First-stage visual sample only. WorldMap remains the ordinary game viewer.
public partial class RegionMap : Control
{
    public RegionLayout Layout { get; private set; } = null!;
    public Action<RegionPlan?>? SelectionChanged;
    public bool ShowBounds;
    public RegionPlan? Selected { get; private set; }
    public float Zoom => _scale;
    private ImageTexture _ground = null!;
    private readonly Dictionary<string, Texture2D> _textures = new();
    private Vector2 _offset;
    private float _scale = 1;
    private bool _fit = true;
    private bool _drag;
    private Font _font = null!;
    private Vector2 _press;

    public override void _Ready()
    {
        ClipContents = true;
        TextureFilter = TextureFilterEnum.Nearest;
        MouseFilter = MouseFilterEnum.Stop;
        _font = GetThemeFont("font", "Label");
        Resized += () => { if (_fit && Layout != null) Fit(); };
    }

    public void SetLayout(RegionLayout layout)
    {
        Layout = layout;
        Selected = null;
        var image = Image.CreateEmpty(layout.Width * 128, layout.Height * 128, false, Image.Format.Rgb8);
        for (int y = 0; y < image.GetHeight(); y++)
            for (int x = 0; x < image.GetWidth(); x++)
            {
                float wx = x * 2, wy = y * 2;
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
                // Fine grain is world-anchored, never resampled on camera moves.
                float grain = ((int)(layout.Hash(x / 2, y / 2, 13) % 9) - 4) * .008f;
                image.SetPixel(x, y, grain > 0 ? color.Lightened(grain) : color.Darkened(-grain));
            }
        _ground?.Dispose();
        _ground = ImageTexture.CreateFromImage(image);
        image.Dispose();
        Fit();
    }

    public void Fit()
    {
        _fit = true;
        _scale = Math.Clamp(Math.Min((Size.X - 80) / (Layout.Width * 256), (Size.Y - 70) / (Layout.Height * 256)), .15f, 4);
        _offset = (Size - new Vector2(Layout.Width * 256, Layout.Height * 256) * _scale) / 2;
        QueueRedraw();
    }

    public void Focus(int x, int y)
    {
        Selected = Layout.Regions.FirstOrDefault(r => r.Input.X == x && r.Input.Y == y);
        _fit = false;
        _scale = Math.Clamp(Math.Min(Size.X / 440, Size.Y / 320), .5f, 3);
        _offset = Size / 2 - new Vector2(x * 256 + 128, y * 256 + 128) * _scale;
        SelectionChanged?.Invoke(Selected);
        QueueRedraw();
    }

    public Vector2 CellCenter(int x, int y) => _offset + new Vector2(x * 256 + 128, y * 256 + 128) * _scale;

    public override void _GuiInput(InputEvent input)
    {
        if (input is InputEventMouseButton b)
        {
            if (b.ButtonIndex is MouseButton.Right or MouseButton.Middle) _drag = b.Pressed;
            if (b.ButtonIndex == MouseButton.Left)
            {
                if (b.Pressed) _press = b.Position;
                else if (_press.DistanceTo(b.Position) < 5)
                {
                    var p = (b.Position - _offset) / (_scale * 256);
                    Selected = Layout.Regions.FirstOrDefault(r => r.Input.X == (int)MathF.Floor(p.X) && r.Input.Y == (int)MathF.Floor(p.Y));
                    SelectionChanged?.Invoke(Selected);
                    QueueRedraw();
                }
            }
            if (b.Pressed && b.ButtonIndex is MouseButton.WheelUp or MouseButton.WheelDown)
            {
                var point = (b.Position - _offset) / _scale;
                _scale = Math.Clamp(_scale * (b.ButtonIndex == MouseButton.WheelUp ? 1.18f : 1 / 1.18f), .15f, 4);
                _offset = b.Position - point * _scale;
                _fit = false;
                QueueRedraw();
            }
        }
        if (input is InputEventMouseMotion m && _drag)
        {
            _offset += m.Relative;
            _fit = false;
            QueueRedraw();
        }
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
        DrawRect(new Rect2(Vector2.Zero, Size), new Color("1c2b29"));
        if (Layout == null) return;
        DrawSetTransform(_offset, 0, Vector2.One * _scale);
        DrawRect(new Rect2(-5, -5, Layout.Width * 256 + 10, Layout.Height * 256 + 10), new Color("495c44"));
        DrawTextureRect(_ground, new Rect2(0, 0, Layout.Width * 256, Layout.Height * 256), false);
        var objects = new List<(float Y, Action Draw)>();
        foreach (var r in Layout.Regions)
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
            foreach (var rock in r.Rocks)
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
                objects.Add((origin.Y + b.Bounds.Y + b.Bounds.Height, () => DrawStructure(b, origin)));
            }
            foreach (var tree in r.Trees)
            {
                var t = tree;
                var p = origin + new Vector2(t.Foot.X, t.Foot.Y);
                objects.Add((p.Y, () =>
                {
                    DrawEllipse(p + new Vector2(-2, 0), new Vector2(7, 2), new Color(0, 0, 0, .12f));
                    Sprite(t.Sheet, (p - new Vector2(8, 16)).Floor(), t.Variant);
                }));
            }
            foreach (var person in r.People)
            {
                var p = origin + new Vector2(person.X, person.Y);
                objects.Add((p.Y, () => DrawPerson(p)));
            }
            if (ShowBounds) DrawRect(new Rect2(origin, new Vector2(256, 256)), new Color(1, 1, .8f, .22f), false, 1 / _scale);
        }
        foreach (var item in objects.OrderBy(o => o.Y)) item.Draw();
        if (Selected != null)
            DrawRect(new Rect2(Selected.Input.X * 256, Selected.Input.Y * 256, 256, 256), new Color("e8db9a"), false, 2 / _scale);
        DrawSetTransform(Vector2.Zero);
        foreach (var r in Layout.Regions)
        {
            var at = _offset + new Vector2(r.Input.X * 256 + 12, r.Input.Y * 256 + 12) * _scale;
            if (at.X < -200 || at.X > Size.X || at.Y < 0 || at.Y > Size.Y - 20) continue;
            var textSize = _font.GetStringSize(r.Input.Name, fontSize: 14);
            DrawRect(new Rect2(at, textSize + new Vector2(14, 10)), new Color(.09f, .16f, .12f, .82f));
            DrawString(_font, at + new Vector2(7, 19), r.Input.Name, fontSize: 14, modulate: new Color("eee4b9"));
        }
        DrawScaleBar();
    }

    private void DrawEllipse(Vector2 center, Vector2 radius, Color color)
    {
        var points = Enumerable.Range(0, 16).Select(i => center + new Vector2(MathF.Cos(i * Mathf.Tau / 16) * radius.X, MathF.Sin(i * Mathf.Tau / 16) * radius.Y)).ToArray();
        DrawColoredPolygon(points, color);
    }

    private void DrawStructure(Structure s, Vector2 origin)
    {
        var p = (origin + new Vector2(s.Bounds.X, s.Bounds.Y)).Floor();
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

    private void DrawScaleBar()
    {
        float length = 256 * _scale;
        var p = new Vector2(24, Size.Y - 24);
        DrawRect(new Rect2(p - new Vector2(10, 30), new Vector2(Math.Max(length + 20, 260), 45)), new Color(.09f, .16f, .12f, .9f));
        DrawLine(p, p + new Vector2(length, 0), new Color("e7deb4"), 2);
        DrawLine(p + new Vector2(0, -4), p + new Vector2(0, 4), new Color("e7deb4"), 2);
        DrawLine(p + new Vector2(length, -4), p + new Vector2(length, 4), new Color("e7deb4"), 2);
        DrawString(_font, p + new Vector2(0, -9), "1 地区 = 16 × 16 视觉小格", fontSize: 13, modulate: new Color("e7deb4"));
    }
}
