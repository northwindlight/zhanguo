using Godot;
using System;
using System.Collections.Generic;

public static class RtsArt
{
    private static readonly Dictionary<string,Texture2D> Textures=new();
    public static Texture2D Texture(string name)
    {
        if(!Textures.TryGetValue(name,out var texture))Textures[name]=texture=GD.Load<Texture2D>($"res://assets/rts-ui-v1/{name}.svg");
        return texture;
    }
    public static bool Special(string id)=>id is "castle" or "lumberyard" or "woodpower" or "oilpower" or "supplyfactory" or "equipmentfactory";
    public static UnifiedArt.Entry Entry(string id)=>id is "castle" or "lumberyard"?
        new(new Rect2I(0,0,48,56),new Vector2(24,48),new Rect2I(8,24,32,24),new Vector2I(48,56),1,1,1,1,1,null):
        new(new Rect2I(0,0,64,64),new Vector2(32,56),new Rect2I(8,24,48,32),new Vector2I(64,64),1,1,1,1,1,new Vector2(50,6));
    public static Texture2D Building(string kind)
    {
        string art=UnifiedArt.Building(kind,"house");
        if(Special(art))return Texture(art);
        var e=UnifiedArt.Get(art);return new AtlasTexture { Atlas=UnifiedArt.Atlas,Region=new Rect2(e.Rect.Position,e.Cell) };
    }
    public static Texture2D Unit(string kind,Color color)
    {
        string role=kind.EndsWith("cavalry",StringComparison.Ordinal)?"cavalry":kind.EndsWith("militia",StringComparison.Ordinal)?"axe":"spear";
        var e=UnifiedArt.Get("actor-"+role);
        return new AtlasTexture { Atlas=UnifiedArt.Actor(role,color),Region=new Rect2(Vector2.Zero,e.Cell) };
    }
    public static Texture2D Resource(string key)=>Texture(key.Split(':')[^1] switch { "gold"=>"gold","food"=>"food","wood"=>"wood","ore"=>"ore","oil"=>"oil","equipment"=>"equipment","supply"=>"supply","farmland"=>"farmland","energy"=>"energy",_=>"resource" });
}
