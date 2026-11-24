using Godot;
using System.Collections.Generic;

// The user's prepared pixel assets are shared by controls and map overlays.
public static class HumanArt
{
    private static readonly Dictionary<string,Texture2D> Cache=new();
    public static Texture2D Texture(string name)
    {
        if(!Cache.TryGetValue(name,out var value))Cache[name]=value=GD.Load<Texture2D>($"res://assets/human-ui-v1/png/{name}.png");
        return value;
    }
    // 按钮与面板改用漆金皮肤；地图叠加物（迷雾、军团标记、复选）仍取像素素材。
    public static void Style(Button button,string icon="",string variant="")
    {
        UiSkin.Style(button,icon,variant);
        if(icon!=""&&!UiSkin.HasIcon(icon)){button.Icon=Texture("icon-"+icon);button.AddThemeConstantOverride("icon_max_width",22);}
    }
    public static StyleBox Box(string name)=>name switch {
        "panel"=>UiSkin.Box("panel"),
        "button-selected"=>UiSkin.Box("button-selected"),
        "button-danger"=>UiSkin.Box("button-danger"),
        _ when name.StartsWith("button-")=>UiSkin.Box(name),
        _=>new StyleBoxTexture { Texture=Texture(name),TextureMarginLeft=4,TextureMarginRight=4,TextureMarginTop=4,TextureMarginBottom=4,
            ContentMarginLeft=10,ContentMarginRight=10,ContentMarginTop=8,ContentMarginBottom=8 } };
}
