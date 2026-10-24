using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

// One atlas, one world pixel scale. Metadata owns anchors and action lengths.
public static class UnifiedArt
{
    public sealed record Entry(Rect2I Rect, Vector2 Anchor, Rect2I Footprint, Vector2I Cell,
        int Columns, int Rows, int Idle, int Walk, int Act, Vector2? Effect);
    private static readonly Dictionary<string,Entry> Entries=new();
    private static readonly Dictionary<string,Texture2D> Actors=new();
    private static Texture2D? _atlas;
    private static Image? _source;
    public static Texture2D Atlas
    {
        get
        {
            if(_atlas!=null)return _atlas;
            _atlas=GD.Load<Texture2D>("res://assets/unified-v1/atlas.png");
            using var doc=JsonDocument.Parse(FileAccess.GetFileAsString("res://assets/unified-v1/manifest.json"));
            foreach(var a in doc.RootElement.GetProperty("assets").EnumerateArray())
            {
                int[] Values(string key,int[] fallback)=>a.TryGetProperty(key,out var v)?System.Linq.Enumerable.ToArray(System.Linq.Enumerable.Select(v.EnumerateArray(),n=>n.GetInt32())):fallback;
                var rect=Values("atlas_rect",new[]{0,0,0,0});var anchor=Values("anchor",new[]{0,0});
                var fp=Values("footprint",new[]{0,0,0,0});var cell=Values("cell",new[]{rect[2],rect[3]});
                int frames(string action,int fallback)=>a.TryGetProperty("actions",out var actions)?actions.GetProperty(action).GetProperty("frames").GetInt32():fallback;
                var effect=Values("effect_anchor",Array.Empty<int>());
                Entries[a.GetProperty("id").GetString()!]=new(new(rect[0],rect[1],rect[2],rect[3]),new(anchor[0],anchor[1]),new(fp[0],fp[1],fp[2],fp[3]),new(cell[0],cell[1]),
                    a.TryGetProperty("columns",out var c)?c.GetInt32():1,a.TryGetProperty("rows",out var r)?r.GetInt32():1,frames("idle",1),frames("walk",1),frames("act",1),effect.Length==2?new Vector2(effect[0],effect[1]):null);
            }
            return _atlas;
        }
    }
    public static Entry Get(string id){_=Atlas;return Entries[id];}
    public static void Draw(CanvasItem layer,string id,Vector2 foot,int frame=0,int row=0,Color? color=null,float scale=1)
    {
        var e=Get(id);var src=new Rect2(e.Rect.Position+new Vector2I(frame*e.Cell.X,row*e.Cell.Y),e.Cell);
        layer.DrawTextureRectRegion(Atlas,new Rect2((foot-e.Anchor*scale).Round(),(Vector2)e.Cell*scale),src,color);
    }
    public static string Building(string art,string kind)=>art switch
    {
        var k when k.Contains("town-hall")=>"townhall",
        var k when k.Contains("farm")=>"farm",
        var k when k.Contains("oil-well")=>"oilwell",
        var k when k.Contains("mine")=>"mine",
        var k when k.Contains("lumber")=>"lumberyard",
        var k when k.Contains("barracks")||k.Contains("castle")=>"barracks",
        var k when k.Contains("factory")||k.Contains("power-plant")=>"factory",
        var k when k.Contains("warehouse")||k.Contains("granary")=>"granary",
        var k when k.Contains("workshop")||k.Contains("academy")||k.Contains("tower")=>"workshop",
        _=>kind=="farm"?"farm":kind=="mine"?"mine":"house-0"
    };
    public static Texture2D Actor(string role,Color color)
    {
        string key=role+color.ToHtml(false);
        if(Actors.TryGetValue(key,out var cached))return cached;
        // Replace only the three national cloth slots; skin, metal, horse and
        // outlines retain the source palette. Never tint the whole character.
        var e=Get("actor-"+role);
        _source??=Atlas.GetImage();using var image=_source.GetRegion(e.Rect);
        bool civilian=role is "resident" or "worker";
        Color[] source=civilian?new Color[]{new("3e5c60"),new("63868a"),new("8da6a1")}:
            new Color[]{new("824d43"),new("b46e53"),new("d19064")};
        Color[] target={color.Darkened(.28f),color,color.Lightened(.23f)};
        for(int y=0;y<image.GetHeight();y++)for(int x=0;x<image.GetWidth();x++)
        {
            var p=image.GetPixel(x,y);if(p.A<.5f)continue;
            for(int i=0;i<source.Length;i++)if(p.IsEqualApprox(source[i])){image.SetPixel(x,y,target[i]);break;}
        }
        return Actors[key]=ImageTexture.CreateFromImage(image);
    }
    public static int ActorCacheCount=>Actors.Count;
}
