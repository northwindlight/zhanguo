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
    [JsonPropertyName("live_world")] public bool LiveWorld { get; set; }
    public List<RegionInput> Regions { get; set; } = new();

    public static SampleInput Parse(string json)
    {
        var input = JsonSerializer.Deserialize<SampleInput>(json,
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true })
            ?? throw new InvalidDataException("样板输入为空");
        if (input.LayoutVersion != 2 || input.Regions.Count == 0 ||
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
    public bool Passable { get; set; } = true;
    // Fixed 0..100 visual abundance, synthetic or normalized from observer facts.
    // These are not game rule limits.
    public int Wood { get; set; }
    public int Gold { get; set; }
    public int Oil { get; set; }
    public int Ore { get; set; }
    public int People { get; set; }
    public string OwnerColor { get; set; }="";
    public string Owner { get; set; }="";
    public List<BuildingInput> Buildings { get; set; } = new();
}

public sealed class BuildingInput
{
    public string Id { get; set; } = "";
    public string Kind { get; set; } = "house";
    public int Remaining { get; set; }
    // Original observer kind selects art and the civic hall's visual footprint.
    public string Art { get; set; } = "";
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
public sealed record Structure(string Id, string Kind, Footprint Bounds, int Count = 1, int Remaining = 0, string Art = "");
public sealed record Tree(Point Foot, int Variant, string Sheet);
public sealed record TownLot(Footprint Bounds,int Variant,string Kind="house");
public sealed class RegionPlan
{
    public RegionInput Input { get; init; } = new();
    public List<Structure> Structures { get; } = new();
    public List<Tree> Trees { get; } = new();
    public List<Point> People { get; } = new();
    public List<Point> Gold { get; } = new();
    public List<Point> Iron { get; } = new();
    public List<Point> Rocks { get; } = new();
    public List<Footprint> Outcrops { get; } = new();
    public Footprint? CivicArea { get; set; }
    public Footprint? Plaza { get; set; }
    public List<TownLot> TownLots { get; }=new();
    public List<Footprint> OilPatches { get; }=new();
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
    public string GeometryKey { get; }
    public RoadNetwork Roads { get; }

    public RegionLayout(SampleInput input, RegionLayout? previous = null)
    {
        Input = input;
        _regions = input.Regions.ToDictionary(r => (r.X, r.Y));
        Width = input.Regions.Max(r => r.X) + 1;
        Height = input.Regions.Max(r => r.Y) + 1;
        GeometryKey = $"{input.Seed}/{input.LayoutVersion}/{input.LiveWorld}/" + string.Join(";",
            input.Regions.OrderBy(r=>r.Y).ThenBy(r=>r.X).Select(r=>$"{r.X},{r.Y},{r.Terrain},{r.Passable},{r.Wood},{r.Gold},{r.Oil},{r.Ore}"));
        var reusable = previous?.GeometryKey == GeometryKey
            ? previous.Regions.ToDictionary(r=>(r.Input.X,r.Input.Y)) : new Dictionary<(int,int),RegionPlan>();
        Regions = input.Regions.OrderBy(r => r.Y).ThenBy(r => r.X).Select(r =>
        {
            if(reusable.TryGetValue((r.X,r.Y),out var old) && old.Input.Name==r.Name && old.Input.People==r.People&&old.Input.OwnerColor==r.OwnerColor&&old.Input.Owner==r.Owner &&
                old.Input.Buildings.Select(b=>(b.Id,b.Kind,b.Art,b.Remaining)).SequenceEqual(r.Buildings.Select(b=>(b.Id,b.Kind,b.Art,b.Remaining))))return old;
            return Build(r,old);
        }).ToArray();
        Roads=new RoadNetwork(this,previous?.Roads);
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

    public float ValueNoise(float x, float y, int salt)
    {
        int ix = (int)MathF.Floor(x), iy = (int)MathF.Floor(y);
        float fx = Smooth(x - ix), fy = Smooth(y - iy);
        return Lerp(Lerp(Noise(ix, iy, salt), Noise(ix + 1, iy, salt), fx),
            Lerp(Noise(ix, iy + 1, salt), Noise(ix + 1, iy + 1, salt), fx), fy);
    }

    // Evaluate in global coordinates: both sides of every shared edge sample
    // the same field, including different neighboring terrain types.
    public float Elevation(float x, float y)
    {
        float strength = Interpolate(x, y, static r => r.Terrain == "mountain" ? 1 : r.Terrain == "hill" ? .45f : 0);
        // Warp valleys in two dimensions, then vary the peaks independently.
        // The periodic distance is saturated at its wrap, leaving shared edges continuous.
        float warpedY = y + (ValueNoise(x / 220, y / 220, 21) - .5f) * 108
            + (ValueNoise(x / 75, y / 75, 22) - .5f) * 24;
        float phase = warpedY / RegionSize - .55f;
        float distance = MathF.Abs(phase - MathF.Floor(phase + .5f)) * RegionSize;
        float width = 24 + ValueNoise(x / 170, y / 170, 26) * 15;
        float ridge = Smooth(Math.Clamp((distance - width) / 30, 0, 1));
        float peaks = .30f + .70f * ValueNoise(x / 83, y / 83, 24) + .22f * ValueNoise(x / 37, y / 37, 25);
        float height = peaks * ridge * strength;
        if(Input.LiveWorld)
        {
            float wx=x+(ValueNoise(x/470,y/470,85)-.5f)*150;
            float wy=y+(ValueNoise(x/470,y/470,86)-.5f)*150;
            float ridgeField=MathF.Abs(2*ValueNoise(wx/220,wy/220,87)-1);
            height=strength*MathF.Pow(ridgeField,.8f)*(.4f+.7f*ValueNoise(x/120,y/120,89));
            float high = Interpolate(x,y,static r=>r.Terrain=="high-mountain"?1:0);
            height += high*(.42f+.62f*ValueNoise(x/180,y/180,88));
            height *= 1-Water(x,y);
        }
        return Math.Clamp(height, 0, 1);
    }

    public float Water(float x,float y)
    {
        if(!Input.LiveWorld)return 0;
        float water=Interpolate(x,y,static r=>r.Terrain=="ocean"?1:0);
        water += (ValueNoise(x/90,y/90,91)-.5f)*.3f*water*(1-water)*4;
        float edge=Math.Min(Math.Min(x,y),Math.Min(Width*256-x,Height*256-y));
        // The sealed rim ends in offshore shallows instead of a cut rectangle.
        float fringe=80+ValueNoise(x/260,y/260,92)*120;
        water=Math.Max(water,1-Math.Clamp(edge/fringe,0,1));
        return Smooth(Math.Clamp((water-.3f)/.4f,0,1));
    }
    public float Sand(float x,float y)=>Interpolate(x,y,static r=>r.Terrain=="desert"?1:0);

    private RegionInput? RegionAt(int x, int y) => _regions.GetValueOrDefault((Math.Clamp(x, 0, Width - 1), Math.Clamp(y, 0, Height - 1)));

    private float Interpolate(float x, float y, Func<RegionInput, float> read)
    {
        float sx = x / RegionSize - .5f, sy = y / RegionSize - .5f;
        int ix = (int)MathF.Floor(sx), iy = (int)MathF.Floor(sy);
        float Read(int cx, int cy) => RegionAt(cx, cy) is { } r ? read(r) : 0;
        return Lerp(Lerp(Read(ix, iy), Read(ix + 1, iy), Smooth(sx - ix)),
            Lerp(Read(ix, iy + 1), Read(ix + 1, iy + 1), Smooth(sx - ix)), Smooth(sy - iy));
    }

    public float WoodDensity(float x, float y)
    {
        int cx = (int)MathF.Floor(x / RegionSize), cy = (int)MathF.Floor(y / RegionSize);
        var own = RegionAt(cx, cy);
        if (own == null || own.Wood == 0) return 0;
        float density = Interpolate(x, y, static r => r.Wood) / 100;
        // Positive-resource neighbors blend. Near a zero-resource region, taper
        // inside the resource-bearing side rather than invent trees across it.
        float lx = x - cx * RegionSize, ly = y - cy * RegionSize;
        if (RegionAt(cx - 1, cy)?.Wood == 0) density *= Smooth(Math.Clamp(lx / 42, 0, 1));
        if (RegionAt(cx + 1, cy)?.Wood == 0) density *= Smooth(Math.Clamp((256 - lx) / 42, 0, 1));
        if (RegionAt(cx, cy - 1)?.Wood == 0) density *= Smooth(Math.Clamp(ly / 42, 0, 1));
        if (RegionAt(cx, cy + 1)?.Wood == 0) density *= Smooth(Math.Clamp((256 - ly) / 42, 0, 1));
        return density;
    }

    private Point TreeCandidate(int x, int y) => new((x + Noise(x, y, 50)) * 16, (y + Noise(x, y, 51)) * 16);

    private bool SpacedCandidate(int x, int y, Point p)
    {
        uint priority = Hash(x, y, 52);
        for (int ny = y - 1; ny <= y + 1; ny++)
            for (int nx = x - 1; nx <= x + 1; nx++)
            {
                if (nx == x && ny == y) continue;
                uint other = Hash(nx, ny, 52);
                if (other > priority || (other == priority && (ny > y || (ny == y && nx > x)))) continue;
                var q = TreeCandidate(nx, ny);
                if ((p.X - q.X) * (p.X - q.X) + (p.Y - q.Y) * (p.Y - q.Y) < 256) return false;
            }
        return true;
    }
    private static float Smooth(float v) => v * v * (3 - 2 * v);
    private static float Lerp(float a, float b, float t) => a + (b - a) * t;

    public bool CanBuild(RegionInput region, Footprint bounds)
    {
        if(!region.Passable)return false;
        if (bounds.X < 8 || bounds.Y < 8 || bounds.X + bounds.Width > RegionSize - 8 || bounds.Y + bounds.Height > RegionSize - 8) return false;
        for (float y = bounds.Y - 3; y <= bounds.Y + bounds.Height + 3; y += 2)
            for (float x = bounds.X - 3; x <= bounds.X + bounds.Width + 3; x += 2)
                if ((!Input.LiveWorld && Elevation(region.X * RegionSize + x, region.Y * RegionSize + y) > .18f) ||
                    Water(region.X*RegionSize+x,region.Y*RegionSize+y)>.15f) return false;
        return true;
    }

    private RegionPlan Build(RegionInput r,RegionPlan? previous=null)
    {
        var plan = new RegionPlan { Input = r };
        if(!r.Passable)return plan;
        if (r.Buildings.Select(b => b.Id).Distinct().Count() != r.Buildings.Count)
            throw new InvalidDataException("样板设施 ID 重复");
        // Stable candidates, independent of resource abundance and camera.
        var candidates = (from y in Enumerable.Range(0, 15)
                          from x in Enumerable.Range(0, 14)
                          select new Point(16 + x * 16, 16 + y * 15))
            .OrderBy(p => MathF.Abs(p.X - 125) + MathF.Abs(p.Y - 135) + Noise((int)p.X + r.X * 256, (int)p.Y + r.Y * 256, 5) * 45 +
                (Input.LiveWorld?Elevation(r.X*256+p.X,r.Y*256+p.Y)*600:0)).ToArray();
        bool Hall(BuildingInput b)=>Input.LiveWorld&&b.Art.EndsWith("town-hall",StringComparison.Ordinal);
        foreach (var b in r.Buildings.OrderBy(b=>Hall(b)?0:1).ThenBy(b => b.Id, StringComparer.Ordinal))
        {
            bool hall=Hall(b);
            float w = hall?64:b.Art.Contains("factory")||b.Art.Contains("power-plant")?48:32;
            float h = hall?48:32;
            Footprint? found = previous?.Structures.FirstOrDefault(s=>s.Id==b.Id)?.Bounds;
            if(found is Footprint oldBounds && (oldBounds.Width!=w||oldBounds.Height!=h||!CanBuild(r,oldBounds)||plan.Structures.Any(s=>s.Bounds.Overlaps(oldBounds,7))||plan.TownLots.Any(l=>TownVisualBounds(l).Overlaps(oldBounds,4))||!hall&&plan.CivicArea?.Overlaps(oldBounds,4)==true))found=null;
            foreach (var p in candidates)
            {
                if(found!=null)break;
                var f = new Footprint(p.X, p.Y, w, h);
                var civic=hall?new Footprint(f.X-12,f.Y-26,f.Width+24,f.Height+58):f;
                if (CanBuild(r, civic) && !plan.Structures.Any(s => s.Bounds.Overlaps(f, 7))&&
                    !plan.TownLots.Any(l=>TownVisualBounds(l).Overlaps(f,4))&&(hall||plan.CivicArea?.Overlaps(f,4)!=true)) { found = f; break; }
            }
            if(found==null && Input.LiveWorld && plan.Structures.Count>0)
            {
                int stack=plan.Structures.FindIndex(s=>s.Art==b.Art&&s.Kind==b.Kind);
                if(stack<0)stack=plan.Structures.FindIndex(s=>!s.Art.EndsWith("town-hall",StringComparison.Ordinal));
                if(stack<0)stack=0;
                plan.Structures[stack]=plan.Structures[stack] with { Count=plan.Structures[stack].Count+1 };
                continue;
            }
            if (found == null) throw new InvalidDataException($"样板 {r.Name} 缺少设施平地：{b.Id}");
            plan.Structures.Add(new Structure(b.Id, b.Kind, found.Value,Remaining:b.Remaining,Art:b.Art));
            if(hall)
            {
                var f=found.Value;plan.CivicArea=new Footprint(f.X-12,f.Y-26,f.Width+24,f.Height+58);
                plan.Plaza=new Footprint(f.X-12,f.Y+f.Height+2,f.Width+24,28);
                if(b.Remaining==0&&plan.TownLots.Count==0)
                {
                    var markets=new[]{new Footprint(f.X+f.Width+20,f.Y+f.Height-12,48,24),
                        new Footprint(f.X-68,f.Y+f.Height-12,48,24),new Footprint(f.X+8,f.Y+f.Height+40,48,24)};
                    foreach(var market in markets)
                    {
                        var lot=new TownLot(market,0,"market");
                        if(!CanBuild(r,market)||plan.Structures.Any(s=>StructureVisualBounds(s).Overlaps(TownVisualBounds(lot),2)))continue;
                        plan.TownLots.Add(lot);break;
                    }
                }
            }
        }
        var civicHall=plan.Structures.FirstOrDefault(s=>s.Art.EndsWith("town-hall",StringComparison.Ordinal)&&s.Remaining==0);
        bool Occupied(Footprint f,float margin=3)=>plan.Structures.Any(s=>s.Bounds.Overlaps(f,margin))||plan.CivicArea?.Overlaps(f,margin)==true||plan.TownLots.Any(l=>TownVisualBounds(l).Overlaps(f,margin));
        for (int cy = 0; cy < 16; cy++)
            for (int cx = 0; cx < 16; cx++)
            {
                int gx = r.X * 16 + cx, gy = r.Y * 16 + cy;
                var foot = new Point(cx * 16 + Noise(gx, gy, 1) * 16, cy * 16 + Noise(gx, gy, 2) * 16);
                float elevation = Elevation(r.X * 256 + foot.X, r.Y * 256 + foot.Y);
                var goldBox=GoldBounds(foot);
                if (r.Gold > 0 && Noise(gx, gy, 6) < r.Gold / 100f * .13f && elevation > .2f &&
                    goldBox.X>=2&&goldBox.Y>=2&&goldBox.X+goldBox.Width<=254&&goldBox.Y+goldBox.Height<=254&&
                    !Occupied(goldBox)&&!Occupied(ResourceVisualBounds(r,foot))&&!plan.Gold.Any(g=>GoldBounds(g).Overlaps(goldBox,2)||ResourceVisualBounds(r,g).Overlaps(ResourceVisualBounds(r,foot),2)))
                    plan.Gold.Add(foot);
                var rockBox=RockBounds(foot);
                if (elevation > .25f && Noise(gx, gy, 8) < .12f &&rockBox.X>=2&&rockBox.Y>=2&&rockBox.X+rockBox.Width<=254&&rockBox.Y+rockBox.Height<=254&&
                    !Occupied(RockBounds(foot))&&!Occupied(ResourceVisualBounds(r,foot))) plan.Rocks.Add(foot);
            }
        // Gold-bearing outcrops are distinct deposits. Decorative stones may
        // share the same candidate point, but must never cover their glyphs.
        plan.Rocks.RemoveAll(p=>plan.Gold.Any(g=>(GoldBounds(g).Overlaps(RockBounds(p),3)||ResourceVisualBounds(r,g).Overlaps(ResourceVisualBounds(r,p),3))));
        foreach(var p in candidates.OrderBy(p=>Hash((int)p.X+r.X*256,(int)p.Y+r.Y*256,305)))
        {
            if(plan.Iron.Count>=(r.Ore>0?1+r.Ore/30:0))break;
            var f=RockBounds(p);
            if(!CanBuild(r,f)||Occupied(ResourceVisualBounds(r,p)))continue;
            if(Water(r.X*256+p.X,r.Y*256+p.Y)>.15f||Occupied(f)||plan.Gold.Any(g=>GoldBounds(g).Overlaps(f,3))||
                plan.Rocks.Any(g=>RockBounds(g).Overlaps(f,3))||
                plan.Gold.Concat(plan.Rocks).Concat(plan.Iron).Any(g=>ResourceVisualBounds(r,g).Overlaps(ResourceVisualBounds(r,p),3))||plan.Iron.Any(g=>RockBounds(g).Overlaps(f,12)))continue;
            plan.Iron.Add(p);
        }
        // Globally jittered, spaced candidates; acceptance depends on resource
        // density, but candidate positions/priorities never depend on abundance.
        for (int gy = (int)MathF.Floor(r.Y * 256 / 16f); gy < (int)MathF.Ceiling((r.Y + 1) * 256 / 16f); gy++)
            for (int gx = (int)MathF.Floor(r.X * 256 / 16f); gx < (int)MathF.Ceiling((r.X + 1) * 256 / 16f); gx++)
            {
                var world = TreeCandidate(gx, gy);
                var foot = new Point(world.X - r.X * 256, world.Y - r.Y * 256);
                if (foot.X < 0 || foot.X >= 256 || foot.Y < 0 || foot.Y >= 256 || !SpacedCandidate(gx, gy, world)) continue;
                float clump = .22f + .95f * ValueNoise(world.X / 64, world.Y / 64, 53);
                float density = WoodDensity(world.X, world.Y) * (r.Terrain == "forest" ? .90f : .38f) * clump;
                if (Noise(gx, gy, 54) < density && Elevation(world.X, world.Y) < .25f && Water(world.X,world.Y)<.15f &&
                    !Occupied(TreeBounds(foot))&&
                    !plan.Gold.Any(g=>GoldBounds(g).Overlaps(TreeBounds(foot),2))&&!plan.Iron.Any(g=>RockBounds(g).Overlaps(TreeBounds(foot),2))&&
                    !plan.Gold.Concat(plan.Iron).Any(g=>ResourceVisualBounds(r,g).Overlaps(TreeBounds(foot),2)))
                {
                    bool pine = Hash(gx, gy, 55) % 3 == 0;
                    // Pine sheet: column 0 is a stump, 1 is green, 2 is snow.
                    plan.Trees.Add(new Tree(foot, pine ? 1 : (int)(Hash(gx, gy, 4) % 3) + 1, pine ? "pine-trees" : "trees"));
                }
            }
        for (int y = 0; y < 4; y++)
            for (int x = 0; x < 4; x++)
            {
                var f = new Footprint(8 + x * 48 + Noise(r.X * 4 + x, r.Y * 4 + y, 72) * 18,
                    8 + y * 52 + Noise(r.X * 4 + x, r.Y * 4 + y, 73) * 20, 64, 48);
                bool rock = true;
                for (float py = f.Y; py <= f.Y + f.Height; py += 4)
                    for (float px = f.X; px <= f.X + f.Width; px += 4)
                        if (Elevation(r.X * 256 + px, r.Y * 256 + py) < .30f) rock = false;
                if (rock && plan.Outcrops.Count < 3 && !Occupied(f)&&!plan.Outcrops.Any(other => other.Overlaps(f, 8))&&
                    !plan.Gold.Any(g=>GoldBounds(g).Overlaps(f,3))&&!plan.Iron.Any(g=>RockBounds(g).Overlaps(f,3))) plan.Outcrops.Add(f);
            }
        if(r.Oil>0)
            foreach(var point in candidates.OrderBy(p=>Hash((int)p.X+r.X*256,(int)p.Y+r.Y*256,98)))
            {
                if(plan.OilPatches.Count>=1+r.Oil/25)break;
                var f=new Footprint(point.X,point.Y,16+r.Oil/10f,11+r.Oil/16f);
                if(CanBuild(r,f)&&!Occupied(f,4)&&!plan.Gold.Any(g=>GoldBounds(g).Overlaps(f,4))&&
                    !plan.Iron.Any(g=>RockBounds(g).Overlaps(f,3))&&!plan.Gold.Concat(plan.Iron).Any(g=>ResourceVisualBounds(r,g).Overlaps(f,3))&&
                    !plan.Rocks.Any(p=>RockBounds(p).Overlaps(f,3))&&!plan.Outcrops.Any(p=>p.Overlaps(f,3))&&
                    !plan.Trees.Any(t=>TreeBounds(t.Foot).Overlaps(f,3))&&!plan.OilPatches.Any(p=>p.Overlaps(f,12)))
                    plan.OilPatches.Add(f);
            }
        var peopleCandidates = civicHall==null?candidates.OrderBy(p => (double)Hash((int)p.X + r.X * 256, (int)p.Y + r.Y * 256, 7)):
            candidates.OrderBy(p=>MathF.Abs(p.X-civicHall.Bounds.X-civicHall.Bounds.Width*.5f)+MathF.Abs(p.Y-civicHall.Bounds.Y-civicHall.Bounds.Height-20)+Noise((int)p.X,(int)p.Y,7)*25);
        foreach (var p in peopleCandidates)
        {
            if (plan.People.Count >= r.People) break;
            var body = new Footprint(p.X - 3, p.Y - 8, 6, 8);
            if (CanBuild(r, body) && !plan.Structures.Any(s => StructureVisualBounds(s).Overlaps(body, 4))&& !plan.TownLots.Any(l=>TownVisualBounds(l).Overlaps(body,3))&&!plan.OilPatches.Any(p=>p.Overlaps(body,3)) &&
                !plan.Gold.Any(g=>GoldBounds(g).Overlaps(body,2))&&!plan.Iron.Any(g=>RockBounds(g).Overlaps(body,2))&&
                !plan.Gold.Concat(plan.Iron).Any(g=>ResourceVisualBounds(r,g).Overlaps(body,2))&&
                !plan.Trees.Any(t => TreeBounds(t.Foot).Overlaps(body, 2)) &&
                !plan.Rocks.Any(p => new Footprint(p.X - 8, p.Y - 16, 16, 16).Overlaps(body, 2))) plan.People.Add(p);
        }
        if (plan.People.Count != r.People && !Input.LiveWorld) throw new InvalidDataException($"样板 {r.Name} 缺少人物空地");
        return plan;
    }
    public Point DepositPoint(Point world)
    {
        float x=MathF.Floor(world.X/16)*16+8,y=MathF.Floor(world.Y/16)*16+8;
        int level=PixelTerrain.HeightTier(Elevation(x,y),Water(x,y)>.15f?1:0);
        return new Point(world.X,world.Y-level*8);
    }
    public Footprint ResourceVisualBounds(RegionInput region,Point local)
    {
        var projected=DepositPoint(new Point(region.X*256+local.X,region.Y*256+local.Y));
        return RockBounds(new Point(projected.X-region.X*256,projected.Y-region.Y*256));
    }
    public static Footprint GoldBounds(Point p)=>new(p.X-8,p.Y-14,16,16);
    public static Footprint RockBounds(Point p)=>new(p.X-8,p.Y-14,16,16);
    public static Footprint TreeBounds(Point p)=>new(p.X-16,p.Y-46,32,48);
    public static Footprint TownVisualBounds(TownLot l)=>new(l.Bounds.X-8,l.Bounds.Y-(l.Kind=="market"?24:16),l.Bounds.Width+16,l.Bounds.Height+(l.Kind=="market"?32:24));
    public static Footprint StructureVisualBounds(Structure s)=>s.Bounds.Width>=32?
        new Footprint(s.Bounds.X-8,s.Bounds.Y-(s.Art.EndsWith("town-hall",StringComparison.Ordinal)?24:16),s.Bounds.Width+16,s.Bounds.Height+(s.Art.EndsWith("town-hall",StringComparison.Ordinal)?32:24)):s.Bounds;
}
