using Godot;
using System.Collections.Generic;

// Presentation colors and SVGs only; terrain rules come from player facts.
public static class MapIcons
{
    private static readonly Dictionary<string,Texture2D> Cache=new();
    public static string Key(string terrain)=>terrain.Contains(':')?terrain.Split(':')[^1]:terrain;
    public static Texture2D Texture(string name)
    {
        name=Key(name);
        if(!Cache.TryGetValue(name,out var value))Cache[name]=value=GD.Load<Texture2D>($"res://assets/map-ui-v1/{name}.svg");
        return value;
    }
    public static Color Color(string terrain)=>new(Key(terrain) switch {
        "plain"=>"86ad6e","forest"=>"3e7659","hill"=>"bc9c68","mountain"=>"7e8b9e",
        "high-mountain"=>"d6e3eb","desert"=>"d5b976","ocean"=>"357eae",_=>"263a37" });
}
