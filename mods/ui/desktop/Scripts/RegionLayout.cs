// Pure presentation data. No Godot, Session, rule RNG or mutable world objects.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Zhanguo.Desktop.Layout;

public sealed class SampleInput
{
    public int Seed { get; set; }
    [JsonPropertyName("layout_version")] public int LayoutVersion { get; set; }
    public List<RegionInput> Regions { get; set; } = new();

    public static SampleInput Parse(string json)
    {
        var input = JsonSerializer.Deserialize<SampleInput>(json,
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true })
            ?? throw new InvalidDataException("样板输入为空");
        if (input.LayoutVersion != 1 || input.Regions.Count == 0 ||
            input.Regions.Any(r => r.X < 0 || r.Y < 0 || r.Wood < 0 || r.Wood > 100 || r.Gold < 0 || r.Gold > 100 || r.People < 0) ||
            input.Regions.Select(r => (r.X, r.Y)).Distinct().Count() != input.Regions.Count)
            throw new InvalidDataException("样板版本、坐标或资源范围无效");
        return input;
    }
}

public sealed class RegionInput
{
    public int X { get; set; }
    public int Y { get; set; }
    public string Name { get; set; } = "";
    public string Terrain { get; set; } = "plain";
    // Synthetic, fixed 0..100 visual abundance; these are not game rule limits.
    public int Wood { get; set; }
    public int Gold { get; set; }
    public int People { get; set; }
    public List<BuildingInput> Buildings { get; set; } = new();
}

public sealed class BuildingInput
{
    public string Id { get; set; } = "";
    public string Kind { get; set; } = "house";
}

public readonly record struct Point(float X, float Y);
public readonly record struct Footprint(float X, float Y, float Width, float Height)
{
    public bool Overlaps(Footprint other, float margin = 0) =>
        X - margin < other.X + other.Width && X + Width + margin > other.X &&
        Y - margin < other.Y + other.Height && Y + Height + margin > other.Y;
    public bool Contains(Point p, float margin = 0) =>
        p.X >= X - margin && p.X <= X + Width + margin && p.Y >= Y - margin && p.Y <= Y + Height + margin;
}
public sealed record Structure(string Id, string Kind, Footprint Bounds);
public sealed record Tree(Point Foot, int Variant);
public sealed class RegionPlan
{
    public RegionInput Input { get; init; } = new();
    public List<Structure> Structures { get; } = new();
    public List<Tree> Trees { get; } = new();
    public List<Point> People { get; } = new();
    public List<Point> Gold { get; } = new();
}

public sealed class RegionLayout
{
    public const int RegionSize = 256;
    public const int SmallCell = 16;
    public SampleInput Input { get; }
    public IReadOnlyList<RegionPlan> Regions { get; }
    private readonly Dictionary<(int, int), RegionInput> _regions;
    public int Width { get; }
    public int Height { get; }

    public RegionLayout(SampleInput input)
    {
        Input = input;
        _regions = input.Regions.ToDictionary(r => (r.X, r.Y));
        Width = input.Regions.Max(r => r.X) + 1;
        Height = input.Regions.Max(r => r.Y) + 1;
        Regions = input.Regions.OrderBy(r => r.Y).ThenBy(r => r.X).Select(Build).ToArray();
    }

    // Fixed integer hash instead of runtime-randomized string hashes or Random.
    public uint Hash(int x, int y, int salt = 0)
    {
        unchecked
        {
            uint n = (uint)Input.Seed ^ (uint)(Input.LayoutVersion * 1274126177);
            n ^= (uint)x * 374761393u ^ (uint)y * 668265263u ^ (uint)salt * 2246822519u;
            n = (n ^ (n >> 13)) * 1274126177u;
            return n ^ (n >> 16);
        }
    }
    private float Noise(int x, int y, int salt) => (Hash(x, y, salt) & 65535) / 65535f;

    // Evaluate in global coordinates: both sides of every shared edge sample
    // the same field, including different neighboring terrain types.
    public float Elevation(float x, float y)
    {
        float sx = x / RegionSize - .5f, sy = y / RegionSize - .5f;
        int ix = (int)MathF.Floor(sx), iy = (int)MathF.Floor(sy);
        float fx = Smooth(sx - ix), fy = Smooth(sy - iy);
        float strength = Lerp(Lerp(Strength(ix, iy), Strength(ix + 1, iy), fx),
            Lerp(Strength(ix, iy + 1), Strength(ix + 1, iy + 1), fx), fy);
        // A valley band in each region row; ridges are already saturated near
        // the row boundary, so the change of band does not create a seam.
        float valley = MathF.Floor(y / RegionSize) * RegionSize + 142 + MathF.Sin(x * .008f + (Input.Seed % 97) * .03f) * 18;
        float ridge = Math.Clamp((MathF.Abs(y - valley) - 32) / 54, 0, 1);
        float ripple = MathF.Sin(x * .038f + y * .015f) * .055f + MathF.Sin(x * .019f - y * .03f) * .04f;
        return Math.Clamp((ridge + ripple * ridge) * strength, 0, 1);
    }

    private float Strength(int x, int y)
    {
        if (!_regions.TryGetValue((Math.Clamp(x, 0, Width - 1), Math.Clamp(y, 0, Height - 1)), out var r)) return 0;
        return r.Terrain == "mountain" ? 1 : r.Terrain == "hill" ? .45f : 0;
    }
    private static float Smooth(float v) => v * v * (3 - 2 * v);
    private static float Lerp(float a, float b, float t) => a + (b - a) * t;

    public bool CanBuild(RegionInput region, Footprint bounds)
    {
        if (bounds.X < 8 || bounds.Y < 8 || bounds.X + bounds.Width > RegionSize - 8 || bounds.Y + bounds.Height > RegionSize - 8) return false;
        for (float y = bounds.Y - 3; y <= bounds.Y + bounds.Height + 3; y += 2)
            for (float x = bounds.X - 3; x <= bounds.X + bounds.Width + 3; x += 2)
                if (Elevation(region.X * RegionSize + x, region.Y * RegionSize + y) > .18f) return false;
        return true;
    }

    private RegionPlan Build(RegionInput r)
    {
        var plan = new RegionPlan { Input = r };
        if (r.Buildings.Select(b => b.Id).Distinct().Count() != r.Buildings.Count)
            throw new InvalidDataException("样板设施 ID 重复");
        // Stable candidates, independent of resource abundance and camera.
        var candidates = (from y in Enumerable.Range(0, 15)
                          from x in Enumerable.Range(0, 14)
                          select new Point(16 + x * 16, 16 + y * 15))
            .OrderBy(p => MathF.Abs(p.X - 125) + MathF.Abs(p.Y - 135) + Noise((int)p.X + r.X * 256, (int)p.Y + r.Y * 256, 5) * 45).ToArray();
        foreach (var b in r.Buildings.OrderBy(b => b.Id, StringComparer.Ordinal))
        {
            float w = b.Kind == "farm" ? 24 : b.Kind == "mine" ? 22 : 16;
            float h = b.Kind == "farm" ? 18 : 16;
            Footprint? found = null;
            foreach (var p in candidates)
            {
                var f = new Footprint(p.X, p.Y, w, h);
                if (CanBuild(r, f) && !plan.Structures.Any(s => s.Bounds.Overlaps(f, 7))) { found = f; break; }
            }
            if (found == null) throw new InvalidDataException($"样板 {r.Name} 缺少设施平地：{b.Id}");
            plan.Structures.Add(new Structure(b.Id, b.Kind, found.Value));
        }
        for (int cy = 0; cy < 16; cy++)
            for (int cx = 0; cx < 16; cx++)
            {
                int gx = r.X * 16 + cx, gy = r.Y * 16 + cy;
                var foot = new Point(cx * 16 + 6 + Noise(gx, gy, 1) * 5, cy * 16 + 11 + Noise(gx, gy, 2) * 5);
                float density = r.Wood / 100f * (r.Terrain == "forest" ? .76f : .38f);
                if (Noise(gx, gy, 3) < density && Elevation(r.X * 256 + foot.X, r.Y * 256 + foot.Y) < .25f &&
                    !plan.Structures.Any(s => s.Bounds.Overlaps(new Footprint(foot.X - 8, foot.Y - 15, 16, 16), 3)))
                    plan.Trees.Add(new Tree(foot, (int)(Hash(gx, gy, 4) % 3)));
                if (r.Gold > 0 && Noise(gx, gy, 6) < r.Gold / 100f * .13f &&
                    Elevation(r.X * 256 + foot.X, r.Y * 256 + foot.Y) > .2f)
                    plan.Gold.Add(foot);
            }
        var peopleCandidates = candidates.OrderBy(p => Hash((int)p.X + r.X * 256, (int)p.Y + r.Y * 256, 7));
        foreach (var p in peopleCandidates)
        {
            if (plan.People.Count >= r.People) break;
            var body = new Footprint(p.X - 3, p.Y - 8, 6, 8);
            if (CanBuild(r, body) && !plan.Structures.Any(s => s.Bounds.Overlaps(body, 4)) &&
                !plan.Trees.Any(t => new Footprint(t.Foot.X - 8, t.Foot.Y - 16, 16, 16).Overlaps(body, 2))) plan.People.Add(p);
        }
        if (plan.People.Count != r.People) throw new InvalidDataException($"样板 {r.Name} 缺少人物空地");
        return plan;
    }
}
