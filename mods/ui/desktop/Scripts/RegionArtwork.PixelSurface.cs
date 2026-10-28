using Godot;
using System;
using System.Collections.Generic;
using Zhanguo.Desktop.Layout;

public partial class RegionArtwork
{
    private void BakePixelSurface(RegionLayout layout)
    {
        var cells=new PixelTerrain(layout);
        PixelMountainCells=cells.MountainCells;PixelWaterCells=cells.WaterCells;
        int width=cells.Width*16,height=cells.Height*16;
        using var image=Image.CreateEmpty(width,height,false,Image.Format.Rgba8);
        using var mask=Image.CreateEmpty(width,height,false,Image.Format.Rgb8);
        using var atlas=UnifiedArt.Atlas.GetImage();
        var tints=new Dictionary<(string,int),Image>();
        foreach(var (terrain,tint) in new[]{("forest",new Color(.10f,.31f,.20f,.24f)),("hill",new Color(.59f,.40f,.18f,.20f))})
        for(int weight=1;weight<=8;weight++)
        {var patch=Image.CreateEmpty(16,16,false,Image.Format.Rgba8);patch.Fill(new Color(tint,tint.A*weight/8));tints[(terrain,weight)]=patch;}
        void Stamp(string id,int x,int y,int cropHeight=0)
        {
            var source=UnifiedArt.Get(id).Rect;
            if(cropHeight>0)source.Size=new Vector2I(source.Size.X,cropHeight);
            image.BlendRect(atlas,source,new Vector2I(x,y));
        }
        for(int y=0;y<cells.Height;y++)for(int x=0;x<cells.Width;x++)
        {
            var c=cells.At(x,y);int px=x*16,py=y*16;
            if(c.Water>0)
            {
                int coast=cells.CoastMask(x,y);
                if(coast>0)Stamp($"coast-{coast:00}",px,py);
                else
                {
                    Stamp("water-"+(c.Water==3?"deep":c.Water==2?"mid":"shallow")+"-still",px,py);
                    mask.FillRect(new Rect2I(px,py,16,16),new Color(c.Water/3f,0,0));
                }
            }
            else
            {
                Stamp("ground-"+(c.Sand?"sand":"grass")+"-"+c.Variant,px,py);
                if(!c.Sand)
                {
                    float forest=layout.TerrainBlend(px+8,py+8,"forest"),hill=layout.TerrainBlend(px+8,py+8,"hill");
                    int weight=(int)MathF.Round(Math.Max(forest,hill)*8);
                    if(weight>0)image.BlendRect(tints[(forest>=hill?"forest":"hill",weight)],new Rect2I(0,0,16,16),new Vector2I(px,py));
                }
            }
        }
        foreach(var tint in tints.Values)tint.Dispose();
        // Project each discrete 8px level upward, with complete 8px rock strips
        // for larger drops. The source cap's one-level front face is never stretched.
        FrontFaceCount=0;
        for(int y=0;y<cells.Height;y++)for(int x=0;x<cells.Width;x++)
        {
            var c=cells.At(x,y);if(c.Height==0)continue;
            int px=x*16,py=y*16-c.Height*8;
            int drop=c.Height-cells.At(x,y+1).Height;
            if(drop>0)
            {
                FrontFaceCount++;
                for(int d=1;d<drop;d++)Stamp("cliff-face-"+c.Variant,px,py+16+d*8);
            }
            Stamp($"mountain-{(c.Height>=3?"snow":"rock")}-{cells.LowerMask(x,y):00}",px,py);
            // Clear water under projected rock, and tag only the exposed face
            // for the sample's rock-wall visibility control.
            int top=Math.Max(0,py);
            mask.FillRect(new Rect2I(px,top,16,Math.Min(height-top,Math.Max(0,py+16-top))),Colors.Black);
            if(drop>0)
            {
                int faceTop=Math.Max(0,py+16);
                mask.FillRect(new Rect2I(px,faceTop,16,Math.Min(height-faceTop,drop*8)),new Color(0,1,0));
            }
        }
        foreach(var r in layout.Regions)
            if(r.Input.Frozen)mask.FillRect(new Rect2I(r.Input.X*256,r.Input.Y*256,256,256),new Color(0,0,1));
        _ground=ImageTexture.CreateFromImage(image);
        _waterMask=ImageTexture.CreateFromImage(mask);
        _cliffs=Array.Empty<CliffSegment>();
    }
}
