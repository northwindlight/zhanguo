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
    public static void Style(Button button,string icon="")
    {
        button.TextureFilter=CanvasItem.TextureFilterEnum.Nearest;
        if(icon!=""){button.Icon=Texture("icon-"+icon);button.AddThemeConstantOverride("icon_max_width",24);button.AddThemeConstantOverride("h_separation",6);}
        foreach(var (state,asset) in new[]{("normal","normal"),("hover","hover"),("pressed","pressed"),("disabled","disabled"),("focus","selected")})
            button.AddThemeStyleboxOverride(state,Box("button-"+asset));
    }
    public static StyleBoxTexture Box(string name)=>new() { Texture=Texture(name),
        TextureMarginLeft=4,TextureMarginRight=4,TextureMarginTop=4,TextureMarginBottom=4,
        ContentMarginLeft=10,ContentMarginRight=10,ContentMarginTop=8,ContentMarginBottom=8 };
}
