using Godot;
using System.Collections.Generic;

// Original, editable SVGs. Keep resource handles alive for retained draw calls.
public static class UiArt
{
    private static readonly Dictionary<string,Texture2D> Textures=new();
    public static Texture2D Texture(string name)
    {
        if(!Textures.TryGetValue(name,out var texture))Textures[name]=texture=GD.Load<Texture2D>($"res://assets/ui-v2/{name}.svg");
        return texture;
    }
    public static void Draw(CanvasItem layer,string name,Vector2 position,Vector2 size,Color? color=null)
        =>layer.DrawTextureRect(Texture(name),new Rect2(position.Round(),size),false,color);
}
