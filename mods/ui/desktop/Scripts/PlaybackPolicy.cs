using System;

namespace Zhanguo.Desktop.Layout;

public static class PlaybackPolicy
{
    // Player kind comes from the host, never guessed from the model's name.
    public static float Speed(float selected,string? clientKind,string? scope)=>
        scope=="settlement"||clientKind=="rule"?Math.Max(.1f,selected):1;
}
