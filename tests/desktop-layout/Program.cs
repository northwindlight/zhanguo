using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using Zhanguo.Desktop.Layout;

// Invariants of the actual layout used by Godot, without a renderer or NuGet test dependencies.
static class Program
{
    private static void Require(bool value, string reason)
    {
        if (!value) throw new Exception(reason);
    }

    private static string Projection(RegionLayout layout) => JsonSerializer.Serialize(layout.Regions.Select(r => new
    {
        r.Input.X, r.Input.Y, r.Structures, r.Trees, r.People, r.Gold, r.Rocks, r.Outcrops
    }));

    public static void Main(string[] args)
    {
        string json = File.ReadAllText(args[0]);
        var layout = new RegionLayout(SampleInput.Parse(json));
        Require(layout.Width == 3 && layout.Height == 3 && layout.Regions.Count == 9, "3×3 input coverage");
        Require(Projection(layout) == Projection(new RegionLayout(SampleInput.Parse(json))), "identical input must reproduce every placement");
        var reordered = SampleInput.Parse(json);
        reordered.Regions.Reverse();
        foreach (var r in reordered.Regions) r.Buildings.Reverse();
        Require(Projection(layout) == Projection(new RegionLayout(reordered)), "JSON ordering must not change layout");
        var reseeded = SampleInput.Parse(json); reseeded.Seed++;
        Require(Projection(layout) != Projection(new RegionLayout(reseeded)), "visual seed must affect layout");

        foreach (var r in layout.Regions)
        {
            Require(r.Structures.Count == r.Input.Buildings.Count, "no fixture structure can silently disappear");
            Require(r.People.Count == r.Input.People, "figure count must match fixture");
            foreach (var structure in r.Structures)
            {
                Require(layout.CanBuild(r.Input, structure.Bounds), "building footprint enters mountain or map margin");
                Require(!r.Structures.Any(other => other.Id != structure.Id && other.Bounds.Overlaps(structure.Bounds)), "structure collision");
                Require(!r.Trees.Any(t => new Footprint(t.Foot.X - 8, t.Foot.Y - 15, 16, 16).Overlaps(structure.Bounds)), "tree overlaps structure");
            }
            foreach (var p in r.People)
                Require(!r.Structures.Any(s => s.Bounds.Overlaps(new Footprint(p.X - 3, p.Y - 8, 6, 8))), "figure inside building");
            foreach (var rock in r.Outcrops)
            {
                Require(!r.Structures.Any(s => s.Bounds.Overlaps(rock)), "mountain sprite covers a building");
                for (float y = rock.Y; y <= rock.Y + rock.Height; y += 2)
                    for (float x = rock.X; x <= rock.X + rock.Width; x += 2)
                        Require(layout.Elevation(r.Input.X * 256 + x, r.Input.Y * 256 + y) > .18f, "mountain sprite covers flat valley");
            }
        }

        // Same coordinates and seed: enrichment adds trees without moving existing trees.
        int previous = -1;
        string[] oldTrees = Array.Empty<string>();
        foreach (int abundance in new[] { 0, 25, 55, 100 })
        {
            var input = SampleInput.Parse(json);
            var forest = input.Regions.Single(r => r.X == 1 && r.Y == 1);
            forest.Wood = abundance;
            var plan = new RegionLayout(input).Regions.Single(r => r.Input.X == 1 && r.Input.Y == 1);
            var trees = plan.Trees.Select(t => JsonSerializer.Serialize(t)).ToArray();
            Require(plan.Trees.Count > previous, "forest density must increase with fixed abundance bands");
            Require(oldTrees.All(t => trees.Contains(t)), "abundance must not relocate existing trees");
            if (abundance == 0) Require(plan.Trees.Count == 0, "zero wood must have no resource trees");
            oldTrees = trees; previous = trees.Length;
        }
        Require(layout.Regions.Single(r => r.Input.Gold > 0).Gold.Count > 0, "gold-bearing hills must expose gold");
        Require(layout.Regions.Where(r => r.Input.Gold == 0).All(r => r.Gold.Count == 0), "no invented gold deposits");
        Require(layout.Regions.Sum(r => r.Outcrops.Count) > 0, "generated mountain asset must appear in the sample");
        Require(layout.Regions.SelectMany(r => r.Trees).Any(t => t.Sheet == "pine-trees"), "new pine asset must appear in the sample");
        Require(layout.Regions.SelectMany(r => r.Trees).Where(t => t.Sheet == "pine-trees").All(t => t.Variant == 1), "pine atlas must use the green tree, excluding stump and snow cells");

        var forestTrees = layout.Regions.SelectMany(r => r.Trees.Select(t => new Point(r.Input.X * 256 + t.Foot.X, r.Input.Y * 256 + t.Foot.Y))).ToArray();
        for (int i = 0; i < forestTrees.Length; i++)
            for (int j = i + 1; j < forestTrees.Length; j++)
            {
                var a = forestTrees[i]; var b = forestTrees[j];
                Require((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y) >= 80.99f, "tree candidates overlap even across region borders");
            }
        Require(forestTrees.Min(t => t.Y % 12) < 2 && forestTrees.Max(t => t.Y % 12) > 10, "tree rows must not collapse to aligned scanlines");
        Require(layout.WoodDensity(256.001f, 380) < .0001f, "forest must taper toward a zero-wood neighbor");
        Require(MathF.Abs(layout.WoodDensity(511.999f, 380) - layout.WoodDensity(512.001f, 380)) < .001f, "positive-resource forest edge must blend");

        for (int boundary = 256; boundary <= 512; boundary += 256)
            for (int offset = 0; offset <= 768; offset += 8)
            {
                Require(MathF.Abs(layout.Elevation(boundary - .001f, offset) - layout.Elevation(boundary + .001f, offset)) < .001f, "vertical terrain seam");
                Require(MathF.Abs(layout.Elevation(offset, boundary - .001f) - layout.Elevation(offset, boundary + .001f)) < .001f, "horizontal terrain seam");
            }
        // Mixed terrain directions and a different seed, beyond the showcase's mountain row.
        var neighbors = SampleInput.Parse(json);
        string[] terrains = { "mountain", "plain", "hill", "forest", "mountain", "plain", "hill", "forest", "mountain" };
        neighbors.Seed += 701;
        for (int i = 0; i < neighbors.Regions.Count; i++)
        {
            neighbors.Regions[i].Terrain = terrains[i]; neighbors.Regions[i].Buildings.Clear(); neighbors.Regions[i].People = 0;
        }
        var mixed = new RegionLayout(neighbors);
        for (int b = 256; b <= 512; b += 256)
            for (int p = 0; p <= 768; p += 8)
            {
                Require(MathF.Abs(mixed.Elevation(b - .001f, p) - mixed.Elevation(b + .001f, p)) < .001f, "mixed vertical terrain seam");
                Require(MathF.Abs(mixed.Elevation(p, b - .001f) - mixed.Elevation(p, b + .001f)) < .001f, "mixed horizontal terrain seam");
            }
        ContourChecks.Run(args.Length > 1 ? args[1] : Path.Combine(Path.GetDirectoryName(args[0])!, "terrain-neighbors-v2.json"), layout);
        Console.WriteLine("DESKTOP_LAYOUT_OK: deterministic placement, input counts, safe footprints, resource bands, shared edges");
        foreach (var r in layout.Regions)
            Console.WriteLine($"{r.Input.Name}: facilities={r.Structures.Count} trees={r.Trees.Count} people={r.People.Count} gold={r.Gold.Count} rocks={r.Rocks.Count} mountains={r.Outcrops.Count}");
    }
}
