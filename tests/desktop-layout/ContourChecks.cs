using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Zhanguo.Desktop.Layout;

static class ContourChecks
{
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }

    private static int CheckConnections(IReadOnlyList<CliffSegment> segments, int width, int height)
    {
        var counts = new Dictionary<(float, Point), int>();
        var lines = new HashSet<(float, Point, Point)>();
        foreach (var s in segments)
        {
            Require(s.A != s.B, "zero-length cliff segment");
            Require(!lines.Contains((s.Level, s.B, s.A)) && lines.Add((s.Level, s.A, s.B)), "duplicated cliff edge");
            foreach (var p in new[] { s.A, s.B })
            {
                Require(p.X >= 0 && p.Y >= 0 && p.X <= width && p.Y <= height, "contour leaves world bounds");
                var key = (s.Level, p); counts[key] = counts.GetValueOrDefault(key) + 1;
            }
        }
        int ends = 0;
        foreach (var (key, count) in counts)
        {
            if (count == 2) continue;
            var p = key.Item2;
            Require(count == 1 && (p.X == 0 || p.Y == 0 || p.X == width || p.Y == height), "disconnected interior cliff endpoint");
            ends++;
        }
        return ends;
    }

    public static void Run(string neighborPath, RegionLayout showcase)
    {
        Require(TerrainContours.Build(64, 64, 8, new[] { .3f }, (x, y) => 0).Count == 0, "flat ground invents cliffs");
        var slope = TerrainContours.Build(64, 64, 8, new[] { .301f }, (x, y) => (x + y) / 128);
        Require(CheckConnections(slope, 64, 64) == 2, "a plane's contour must cross the world exactly twice");
        var circle = TerrainContours.Build(64, 64, 8, new[] { .28f, .52f },
            (x, y) => 1 - ((x - 32) * (x - 32) + (y - 32) * (y - 32)) / 900);
        Require(circle.Count > 0 && CheckConnections(circle, 64, 64) == 0, "isolated peaks must form closed contours");
        var saddle = TerrainContours.Build(32, 32, 32, new[] { .5f }, (x, y) => .5f + (x - 16) * (y - 16) / 256);
        Require(saddle.Count == 2 && CheckConnections(saddle, 32, 32) == 4, "ambiguous saddle must not create a crossing or a gap");

        var neighbors = new RegionLayout(SampleInput.Parse(File.ReadAllText(neighborPath)));
        Require(neighbors.Regions.Select(r => r.Input.Terrain).Distinct().Count() == 4, "neighbor sample needs four terrain types");
        foreach (var r in neighbors.Regions)
        {
            Require(r.Structures.Count == r.Input.Buildings.Count && r.People.Count == r.Input.People, "neighbor fixture count mismatch");
            Require(r.Structures.All(s => neighbors.CanBuild(r.Input, s.Bounds)), "neighbor fixture facility on steep ground");
        }
        foreach (var world in new[] { showcase, neighbors })
        {
            var contours = TerrainContours.Build(world.Width * 256, world.Height * 256, 8, new[] { .28f, .52f }, world.Elevation);
            Require(contours.Any(c => c.Depth > 0), "real sample needs visible front cliff faces");
            CheckConnections(contours, world.Width * 256, world.Height * 256);
            foreach (var c in contours.Where(c => c.Depth > 0))
                Require(world.Elevation(c.A.X, c.A.Y + c.Depth) >= .19f && world.Elevation(c.B.X, c.B.Y + c.Depth) >= .19f,
                    "cliff face descends into flat facility ground");
            Console.WriteLine($"CLIFF_CONNECTIONS_OK: {contours.Count} edges, {contours.Count(c => c.Depth > 0)} front faces");
        }
    }
}
