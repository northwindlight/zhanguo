using System;
using System.Collections.Generic;
using System.Linq;

namespace Zhanguo.Desktop.Layout;

public static class ResourceOverlayLayout
{
    // Measured text rectangles, not a fixed character spacing. The caller
    // retains placement for the whole float trajectory, so labels rise together.
    public static Footprint Place(Footprint desired,IReadOnlyList<Footprint> used,float width,float height,float top=70,float bottom=70)
    {
        float w=Math.Min(desired.Width,width-16),h=desired.Height;
        float y=Math.Clamp(desired.Y,top,Math.Max(top,height-bottom-h));
        int levels=Math.Max(1,(int)((y-top)/(h+5))+1);
        for(int n=0;n<160;n++)
        {
            int column=n/levels,row=n%levels;
            float offset=column==0?0:((column+1)/2)*(column%2==1?1:-1)*(w+8);
            var candidate=new Footprint(Math.Clamp(desired.X+offset,8,Math.Max(8,width-w-8)),y-row*(h+5),w,h);
            if(!used.Any(p=>p.Overlaps(candidate,3)))return candidate;
        }
        // A narrow map area between drawers can fall between fixed columns.
        // Fit against actual obstacle edges before falling back to a corner.
        float maxY=Math.Max(top,height-bottom-h);
        var xs=used.SelectMany(p=>new[]{p.X+p.Width+4,p.X-w-4}).Append(desired.X).Append(8).Append(width-w-8)
            .Select(x=>Math.Clamp(x,8,Math.Max(8,width-w-8))).Distinct().ToArray();
        var ys=used.SelectMany(p=>new[]{p.Y+p.Height+4,p.Y-h-4}).Append(y).Append(top).Append(maxY)
            .Select(v=>Math.Clamp(v,top,maxY)).Distinct().ToArray();
        foreach(var candidate in xs.SelectMany(x=>ys.Select(v=>new Footprint(x,v,w,h)))
            .OrderBy(p=>(p.X-desired.X)*(p.X-desired.X)+(p.Y-y)*(p.Y-y)))
            if(!used.Any(p=>p.Overlaps(candidate,3)))return candidate;
        return new Footprint(8,top,w,h);
    }
}
