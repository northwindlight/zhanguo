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
    private Control _worldClip = null!;
    private RegionArtwork _artwork = null!;
    public int ArtworkPasses => _artwork.DrawPasses;
    public int TerrainBuilds => _artwork.TerrainBuilds;
    public int SurfaceCount => _artwork.SurfaceCount;
    public int TextureCount => _artwork.TextureCount;
    public int CliffCount => _artwork.CliffCount;
    public int FrontFaceCount => _artwork.FrontFaceCount;
    public Rect2 WorldScreenRect => new(GlobalPosition + _offset, _worldClip.Size * _scale);
    public bool ShowCliffs
    {
        get => _artwork.ShowCliffs;
        set { _artwork.ShowCliffs = value; _artwork.QueueRedraw(); }
    }
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
        var backdrop = new ColorRect { Color = new Color("1c2b29"), ZIndex = -2, MouseFilter = MouseFilterEnum.Ignore };
        backdrop.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect); AddChild(backdrop);
        _worldClip = new Control { ClipContents = true, ZIndex = -1, MouseFilter = MouseFilterEnum.Ignore,
            TextureFilter = TextureFilterEnum.Nearest };
        AddChild(_worldClip);
        _artwork = new RegionArtwork(); _worldClip.AddChild(_artwork);
        Resized += () => { if (_fit && Layout != null) Fit(); };
    }

    public void SetLayout(RegionLayout layout)
    {
        Layout = layout;
        Selected = null;
        _worldClip.Size = new Vector2(layout.Width * 256, layout.Height * 256);
        _artwork.SetLayout(layout);
        Fit();
    }

    public void Fit()
    {
        _fit = true;
        _scale = Math.Clamp(Math.Min((Size.X - 80) / (Layout.Width * 256), (Size.Y - 70) / (Layout.Height * 256)), .15f, 4);
        _offset = (Size - new Vector2(Layout.Width * 256, Layout.Height * 256) * _scale) / 2;
        UpdateView();
    }

    public void Focus(int x, int y)
    {
        Selected = Layout.Regions.FirstOrDefault(r => r.Input.X == x && r.Input.Y == y);
        _fit = false;
        _scale = Math.Clamp(Math.Min(Size.X / 440, Size.Y / 320), .5f, 3);
        _offset = Size / 2 - new Vector2(x * 256 + 128, y * 256 + 128) * _scale;
        SelectionChanged?.Invoke(Selected);
        UpdateView();
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
                UpdateView();
            }
        }
        if (input is InputEventMouseMotion m && _drag)
        {
            Pan(m.Relative);
        }
    }

    public void Pan(Vector2 delta)
    {
        _offset += delta; _fit = false; UpdateView();
    }

    private void UpdateView()
    {
        _worldClip.Position = _offset;
        _worldClip.Scale = Vector2.One * _scale;
        QueueRedraw();
    }

    public override void _Draw()
    {
        if (Layout == null) return;
        DrawSetTransform(_offset, 0, Vector2.One * _scale);
        DrawRect(new Rect2(0, 0, Layout.Width * 256, Layout.Height * 256), new Color("495c44"), false, 2 / _scale);
        if (ShowBounds)
            foreach (var r in Layout.Regions)
                DrawRect(new Rect2(r.Input.X * 256, r.Input.Y * 256, 256, 256), new Color(1, 1, .8f, .22f), false, 1 / _scale);
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
