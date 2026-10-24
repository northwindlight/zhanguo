using Godot;
using System;

// Drawers overlay the map; their animation never participates in its layout.
public partial class MapDrawer : PanelContainer
{
    public enum Side { Left, Right, Bottom }
    public bool IsOpen { get; private set; }
    public bool IsSettled => _progress==(IsOpen?1:0);
    public Button CloseButton { get; private set; } = null!;
    public VBoxContainer Body { get; private set; } = null!;
    private Side _side;
    private Rect2 _bounds;
    private float _progress;

    public void Setup(string title,Side side)
    {
        _side=side;ZIndex=20;MouseFilter=MouseFilterEnum.Stop;
        var style=new StyleBoxFlat { BgColor=new Color(.055f,.10f,.11f,.96f),BorderColor=new Color("566354"),
            ContentMarginLeft=18,ContentMarginRight=18,ContentMarginTop=12,ContentMarginBottom=16 };
        style.SetBorderWidthAll(1);style.SetCornerRadiusAll(0);AddThemeStyleboxOverride("panel",style);
        var column=new VBoxContainer();column.AddThemeConstantOverride("separation",12);AddChild(column);
        var header=new HBoxContainer();column.AddChild(header);
        var label=new Label { Text=title,SizeFlagsHorizontal=SizeFlags.ExpandFill };
        label.AddThemeFontSizeOverride("font_size",20);label.AddThemeColorOverride("font_color",new Color("ddcf9d"));header.AddChild(label);
        CloseButton=new Button { Text="收起 ×",FocusMode=FocusModeEnum.None };CloseButton.Pressed+=()=>SetOpen(false);header.AddChild(CloseButton);
        Body=new VBoxContainer { SizeFlagsVertical=SizeFlags.ExpandFill };column.AddChild(Body);
        SetOpen(false,false);
    }

    public void SetBounds(Rect2 bounds) { _bounds=bounds;Size=bounds.Size;UpdatePosition(); }
    public void SetOpen(bool open,bool animate=true)
    {
        IsOpen=open;
        if(!animate)_progress=open?1:0;
        Visible=open||_progress>0;
        SetProcess(animate);UpdatePosition();
    }
    public override void _Process(double delta)
    {
        _progress=Mathf.MoveToward(_progress,IsOpen?1:0,(float)delta*5.5f);
        UpdatePosition();
        if(_progress==(IsOpen?1:0)){Visible=IsOpen;SetProcess(false);}
    }
    private void UpdatePosition()
    {
        var closed=_bounds.Position+(_side==Side.Left?new Vector2(-_bounds.Size.X-24,0):
            _side==Side.Right?new Vector2(_bounds.Size.X+24,0):new Vector2(0,_bounds.Size.Y+90));
        float smooth=_progress*_progress*(3-2*_progress);
        Position=closed.Lerp(_bounds.Position,smooth);
    }
}
