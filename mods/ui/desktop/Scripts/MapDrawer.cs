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
    private Label _title=null!;
    public string Title { get=>_title.Text;set=>_title.Text=value; }

    public void Setup(string title,Side side)
    {
        _side=side;ZIndex=20;MouseFilter=MouseFilterEnum.Stop;
        var style=UiSkin.Box("panel");style.ContentMarginLeft=20;style.ContentMarginRight=20;style.ContentMarginTop=16;style.ContentMarginBottom=18;
        AddThemeStyleboxOverride("panel",style);
        var column=new VBoxContainer();column.AddThemeConstantOverride("separation",12);AddChild(column);
        var header=new HBoxContainer();column.AddChild(header);
        var band=UiSkin.TitleBand(title,out var label);band.SizeFlagsHorizontal=SizeFlags.ExpandFill;
        _title=label;header.AddChild(band);
        CloseButton=new Button { Text="",TooltipText="收起",FocusMode=FocusModeEnum.None,CustomMinimumSize=new Vector2(40,36) };
        UiSkin.Style(CloseButton,"close");CloseButton.AddThemeConstantOverride("icon_max_width",18);
        CloseButton.Pressed+=()=>SetOpen(false);header.AddChild(CloseButton);
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
