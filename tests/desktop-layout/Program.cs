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
        r.Input.X, r.Input.Y, r.Structures, r.Trees, r.People, r.Gold
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

        for (int boundary = 256; boundary <= 512; boundary += 256)
            for (int offset = 0; offset <= 768; offset += 8)
            {
                Require(MathF.Abs(layout.Elevation(boundary - .001f, offset) - layout.Elevation(boundary + .001f, offset)) < .001f, "vertical terrain seam");
                Require(MathF.Abs(layout.Elevation(offset, boundary - .001f) - layout.Elevation(offset, boundary + .001f)) < .001f, "horizontal terrain seam");
            }
        Console.WriteLine("DESKTOP_LAYOUT_OK: deterministic placement, input counts, safe footprints, resource bands, shared edges");
        foreach (var r in layout.Regions)
            Console.WriteLine($"{r.Input.Name}: facilities={r.Structures.Count} trees={r.Trees.Count} people={r.People.Count} gold={r.Gold.Count}");
    }
}
