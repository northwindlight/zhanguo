using System;
using System.Collections.Generic;

namespace Zhanguo.Desktop.Layout;

public sealed record CliffSegment(Point A, Point B, float Level, int Depth);

// Marching squares over one global height field. Adjacent cells reuse the same
// edge samples and interpolation direction, including at rule-region borders.
public static class TerrainContours
{
    public static IReadOnlyList<CliffSegment> Build(int width, int height, int step,
        float[] levels, Func<float, float, float> elevation)
    {
        if (width <= 0 || height <= 0 || step <= 0) throw new ArgumentOutOfRangeException(nameof(step));
        int columns = (width + step - 1) / step, rows = (height + step - 1) / step;
        var samples = new float[columns + 1, rows + 1];
        for (int y = 0; y <= rows; y++)
            for (int x = 0; x <= columns; x++) samples[x, y] = elevation(Math.Min(x * step, width), Math.Min(y * step, height));
        var result = new List<CliffSegment>();
        foreach (float level in levels)
            for (int y = 0; y < rows; y++)
                for (int x = 0; x < columns; x++)
                {
                    float x0 = x * step, y0 = y * step;
                    float x1 = Math.Min((x + 1) * step, width), y1 = Math.Min((y + 1) * step, height);
                    float a = samples[x, y], b = samples[x + 1, y], c = samples[x + 1, y + 1], d = samples[x, y + 1];
                    int[] edges = new int[4]; int count = 0;
                    if ((a >= level) != (b >= level)) edges[count++] = 0;
                    if ((b >= level) != (c >= level)) edges[count++] = 1;
                    if ((d >= level) != (c >= level)) edges[count++] = 2;
                    if ((a >= level) != (d >= level)) edges[count++] = 3;
                    Point At(int edge) => edge switch
                    {
                        0 => new(x0 + (x1 - x0) * (level - a) / (b - a), y0),
                        1 => new(x1, y0 + (y1 - y0) * (level - b) / (c - b)),
                        2 => new(x0 + (x1 - x0) * (level - d) / (c - d), y1),
                        _ => new(x0, y0 + (y1 - y0) * (level - a) / (d - a))
                    };
                    void Join(int first, int second)
                    {
                        var p = At(first); var q = At(second);
                        if (MathF.Abs(p.X - q.X) + MathF.Abs(p.Y - q.Y) < .0001f) return;
                        float mx = (p.X + q.X) / 2, my = (p.Y + q.Y) / 2;
                        int depth = elevation(mx, my - 2) > elevation(mx, my + 2) + .001f ? (level > .5f ? 4 : 3) : 0;
                        // The visual wall must stop above flat facility ground.
                        while (depth > 0 && (elevation(p.X, p.Y + depth) < .19f ||
                            elevation(mx, my + depth) < .19f || elevation(q.X, q.Y + depth) < .19f)) depth--;
                        result.Add(new CliffSegment(p, q, level, depth));
                    }
                    if (count == 2) Join(edges[0], edges[1]);
                    else if (count == 4)
                    {
                        // Resolve the saddle consistently within this cell.
                        bool centerHigh = (a + b + c + d) / 4 >= level;
                        if ((a >= level) == centerHigh) { Join(0, 1); Join(2, 3); }
                        else { Join(0, 3); Join(1, 2); }
                    }
                }
        return result;
    }
}
