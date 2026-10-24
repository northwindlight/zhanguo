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
        return new Footprint(8,top,w,h);
    }
}
