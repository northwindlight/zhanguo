using Godot;
using System;
using System.Linq;

public partial class WorldMap
{
    public bool SimpleTerrain { get; private set; }
    public int SimpleVisibleTiles=>_tiles.Values.Count(t=>S(t["visibility"])!="unknown"&&t["terrain"]!=null);
    public void SetTerrainMode(bool simple)
    {
        SimpleTerrain=simple;if(_artwork!=null)_artwork.Visible=!simple;QueueRedraw();
    }
    private void HoverTerrain(Vector2 at)
    {
        var point=(at-_offset)/(_scale*Cell);var p=new Vector2I((int)MathF.Floor(point.X),(int)MathF.Floor(point.Y));
        if(!_tiles.TryGetValue(p,out var tile)){TooltipText="";return;}
        TooltipText=S(tile["visibility"])=="unknown"?$"未探明地块 ({p.X}, {p.Y})":
            $"{S(tile["name"])} ({p.X}, {p.Y}) · {S(tile["terrain_name"])}"+
            (S(tile["visibility"])=="memory"?$"\n历史地图 · 第 {I(tile["sampled"])+1} 期":"")+
            (tile["passable"]?.GetValue<bool>()==false?"\n自然屏障 · 不可通行":"");
    }
    private void DrawTerrainMap()
    {
        foreach(var (point,tile) in _tiles)
        {
            // Never infer terrain from layout, neighbors or the world seed.
            if(S(tile["visibility"])=="unknown"||tile["terrain"]==null)continue;
            var rect=new Rect2(new Vector2(point.X,point.Y)*Cell,Vector2.One*Cell);
            if(!new Rect2(Vector2.Zero,Size).Intersects(new Rect2(_offset+rect.Position*_scale,rect.Size*_scale)))continue;
            string terrain=S(tile["terrain"]);var color=MapIcons.Color(terrain);
            DrawRect(rect,color);DrawRect(rect,new Color("243c35"),false,1/_scale);
            if(Cell*_scale>=26)
            {
                float side=Math.Min(28,Cell*_scale*.42f)/_scale;
                DrawTextureRect(MapIcons.Texture(terrain),new Rect2(rect.GetCenter()-Vector2.One*side*.5f,Vector2.One*side),false);
            }
            if(Cell*_scale>=74)
            {
                string label=S(tile["terrain_name"]);if(label=="")label=S(Frame?["labels"]?[terrain]);
                var at=rect.GetCenter()+new Vector2(0,35/_scale);
                var textSize=_font.GetStringSize(label,fontSize:15);
                DrawRect(new Rect2(at-new Vector2((textSize.X*.5f+5)/_scale,17/_scale),new Vector2((textSize.X+10)/_scale,23/_scale)),new Color("1b302c"));
                DrawSetTransform(Vector2.Zero);
                DrawString(_font,Screen(at)+new Vector2(-textSize.X*.5f,0),label,fontSize:15,modulate:new Color("f3edcc"));
                DrawSetTransform(_offset+_jitter,0,Vector2.One*_scale);
            }
        }
    }
}
