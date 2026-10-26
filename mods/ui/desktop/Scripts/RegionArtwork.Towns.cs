using Godot;
using System;
using Zhanguo.Desktop.Layout;

public partial class RegionArtwork
{
    private void DrawOilPatches(CanvasItem layer,RegionPlan region,Vector2 origin)
    {
        foreach(var patch in region.OilPatches)
        {
            var center=origin+new Vector2(patch.X+patch.Width*.5f,patch.Y+patch.Height*.5f);
            var points=new Vector2[12];
            for(int i=0;i<points.Length;i++)
            {
                float angle=i*Mathf.Tau/points.Length;
                float jitter=.78f+(Layout.Hash((int)center.X+i,(int)center.Y,99)%23)/100f;
                points[i]=center+new Vector2(MathF.Cos(angle)*patch.Width*.5f,MathF.Sin(angle)*patch.Height*.5f)*jitter;
            }
            layer.DrawColoredPolygon(points,new Color("4f503d"));
            for(int i=0;i<points.Length;i++)points[i]=center+(points[i]-center)*.82f;
            layer.DrawColoredPolygon(points,new Color("252d2b"));
            var sheen=new Color(.30f,.40f,.38f,.55f);
            layer.DrawLine(center+new Vector2(-patch.Width*.22f,-1),center+new Vector2(patch.Width*.12f,-2),sheen,1);
            layer.DrawLine(center+new Vector2(-patch.Width*.02f,2),center+new Vector2(patch.Width*.25f,1),new Color("3e424e"),1);
        }
    }
    private void DrawTownGround(CanvasItem layer,RegionPlan region,Vector2 origin)
    {
        if(region.CivicArea is not Footprint area||region.Plaza is not Footprint plaza)return;
        var yard=new Rect2(origin+new Vector2(area.X,area.Y),new Vector2(area.Width,area.Height));
        layer.DrawRect(yard,new Color("9c9d7c"));
        var square=new Rect2(origin+new Vector2(plaza.X,plaza.Y),new Vector2(plaza.Width,plaza.Height));
        layer.DrawRect(square,new Color("aaa58a"));layer.DrawRect(square,new Color("d2c8a1"),false,2);
        for(float x=square.Position.X+8;x<square.End.X;x+=8)
            layer.DrawLine(new Vector2(x,square.Position.Y+2),new Vector2(x,square.End.Y-2),new Color("939681"),.7f);
        for(float y=square.Position.Y+8;y<square.End.Y;y+=8)
            layer.DrawLine(new Vector2(square.Position.X+2,y),new Vector2(square.End.X-2,y),new Color("939681"),.7f);
        // Civic scenery carries no production or defense bonuses.
        var well=square.Position+new Vector2(10,plaza.Height*.5f);
        DrawEllipse(layer,well,new Vector2(4,3),new Color("747f7c"));
        DrawEllipse(layer,well-new Vector2(0,1),new Vector2(2,1.4f),new Color("395c63"));
        foreach(float x in new[]{square.Position.X+22,square.End.X-23})
        {
            layer.DrawRect(new Rect2(new Vector2(x,square.End.Y-6),new Vector2(10,2)),new Color("72563b"));
            layer.DrawLine(new Vector2(x+1,square.End.Y-4),new Vector2(x+1,square.End.Y-2),new Color("554c38"),1);
            layer.DrawLine(new Vector2(x+8,square.End.Y-4),new Vector2(x+8,square.End.Y-2),new Color("554c38"),1);
        }
        foreach(var p in new[]{yard.Position+new Vector2(4,6),new Vector2(yard.End.X-6,yard.Position.Y+6)})
        {
            layer.DrawRect(new Rect2(p,new Vector2(5,14)),new Color("6d8155"));
            layer.DrawRect(new Rect2(p+new Vector2(1,2),new Vector2(3,2)),new Color("c5bc88"));
        }
    }
    private void DrawTownLot(CanvasItem layer,TownLot lot,Vector2 origin)
    {
        var foot=origin+new Vector2(lot.Bounds.X+lot.Bounds.Width*.5f,lot.Bounds.Y+lot.Bounds.Height);
        if(lot.Kind=="market")UiArt.Draw(layer,"market",foot-new Vector2(32,44),new Vector2(64,48));
    }
    private void DrawRoads(CanvasItem layer,RegionPlan region,Vector2 origin)
    {
        foreach(var p in Layout.Roads.For(region))
        {
            var at=origin+new Vector2(p.X,p.Y);
            int height=PixelTerrain.HeightTier(Layout.Elevation(at.X,at.Y));
            UiArt.Draw(layer,"road",at-new Vector2(4,4+height*8),new Vector2(8,8));
        }
    }
    private void DrawTownHallDetails(CanvasItem layer,Structure hall,Vector2 p,Vector2 size)
    {
        var region=_onlyRegion;
        if(region==null)foreach(var r in Layout.Regions)if(r.Structures.Contains(hall)){region=r;break;}
        var color=new Color(region?.Input.OwnerColor is {Length:>0} value?value:"b8c399");
        var flag=p+new Vector2(size.X+7,-13);
        layer.DrawLine(flag,flag+new Vector2(0,32),new Color("6b5a3f"),1.5f);
        layer.DrawColoredPolygon(new[]{flag,flag+new Vector2(14,1),flag+new Vector2(12,12),flag+new Vector2(0,11)},color);
        layer.DrawLine(flag+new Vector2(0,11),flag+new Vector2(12,12),new Color("ead9a7"),1);
        layer.DrawRect(new Rect2(p+new Vector2(size.X*.5f-12,size.Y-2),new Vector2(24,4)),new Color("c2b99b"));
        if(_detailLevel>0&&LabelFont!=null)
        {
            var at=p+new Vector2(size.X*.5f-24,size.Y+18);
            layer.DrawRect(new Rect2(at-new Vector2(3,15),new Vector2(54,22)),new Color(.07f,.10f,.09f,.85f));
            layer.DrawString(LabelFont,at,"市政厅",fontSize:16,modulate:new Color("f1dfad"));
        }
    }
}
