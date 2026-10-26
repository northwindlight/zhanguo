using System;
using System.Collections.Generic;
using System.Linq;

namespace Zhanguo.Desktop.Layout;

// Door-to-door streets on an 8px grid. A route is accepted only if every
// segment stays on one terrace and clears water, structures and deposits.
// Adjacent regions share one verified portal; roads grant no rule movement.
public sealed class RoadNetwork
{
    private sealed class Graph
    {
        public RegionPlan Region { get; }
        public bool[] Safe { get; }=new bool[1024];
        public int[] Height { get; }=new int[1024];
        public HashSet<int> Base { get; }=new();
        public static Point Point(int id)=>new(id%32*8+4,id/32*8+4);
        public Graph(RegionLayout layout,RegionPlan region)
        {
            Region=region;
            if(!region.Input.Passable||region.Structures.Count==0)return;
            for(int id=0;id<1024;id++)
            {
                var p=Point(id);float x=region.Input.X*256+p.X,y=region.Input.Y*256+p.Y;
                Height[id]=PixelTerrain.HeightTier(layout.Elevation(x,y));
                // Test the same projected pixels that the renderer paints,
                // including roofs/eaves rather than only building footprints.
                var box=new Footprint(p.X-4,p.Y-4-Height[id]*8,8,8);
                Safe[id]=layout.Water(x,y)<.15f&&
                    !region.Structures.Any(s=>RegionLayout.StructureVisualBounds(s).Overlaps(box,1))&&!region.TownLots.Any(l=>RegionLayout.TownVisualBounds(l).Overlaps(box,1))&&
                    !region.Gold.Concat(region.Iron).Concat(region.Rocks).Any(r=>layout.ResourceVisualBounds(region.Input,r).Overlaps(box,1))&&
                    !region.OilPatches.Any(r=>r.Overlaps(box,1))&&
                    !region.Trees.Any(t=>new Footprint(t.Foot.X-3,t.Foot.Y-3,6,6).Overlaps(box,1));
            }
            Point Door(Footprint bounds)
            {
                float x=bounds.X+bounds.Width*.5f,y=bounds.Y+bounds.Height+14;
                return new(x,y+PixelTerrain.HeightTier(layout.Elevation(region.Input.X*256+x,region.Input.Y*256+y))*8);
            }
            var halls=region.Structures.Where(s=>s.Art.EndsWith("town-hall",StringComparison.Ordinal)).OrderBy(s=>s.Id,StringComparer.Ordinal);
            var doors=halls.Select(s=>Door(s.Bounds)).Concat(region.TownLots.Select(l=>Door(l.Bounds)))
                .Concat(region.Structures.Where(s=>!s.Art.EndsWith("town-hall",StringComparison.Ordinal)).OrderBy(s=>s.Id,StringComparer.Ordinal).Select(s=>Door(s.Bounds)));
            var entries=new List<int>();
            foreach(var door in doors)
            {
                int start=Enumerable.Range(0,1024).Where(i=>Safe[i]&&i%32>0&&i%32<31&&i/32>0&&i/32<31).Select(i=>(Id:i,P:Point(i)))
                    .Where(v=>MathF.Abs(v.P.X-door.X)<=24&&MathF.Abs(v.P.Y-door.Y)<=24)
                    .OrderBy(v=>MathF.Abs(v.P.X-door.X)+MathF.Abs(v.P.Y-door.Y)).ThenBy(v=>v.Id)
                    .Select(v=>v.Id).DefaultIfEmpty(-1).First();
                if(start>=0&&!entries.Contains(start))entries.Add(start);
            }
            if(entries.Count==0)return;
            Base.Add(entries[0]);entries.RemoveAt(0);
            // A civic main street first, then the closest short feeder. Never
            // string buildings together in construction/ID order.
            if(region.TownLots.Count>0&&entries.Count>0)
            {if(Route(entries[0],Base,24) is int[] civic)Base.UnionWith(civic);entries.RemoveAt(0);}
            while(entries.Count>0)
            {
                int entry=entries.OrderBy(i=>Base.Min(b=>Math.Abs(i%32-b%32)+Math.Abs(i/32-b/32))).ThenBy(i=>i).First();
                entries.Remove(entry);if(Route(entry,Base,18) is int[] path)Base.UnionWith(path);
            }
        }
        private static int[] Neighbors(int id)=>new[]{id-32,id+1,id+32,id-1};
        private static bool Adjacent(int a,int b)=>b>=0&&b<1024&&Math.Abs(a%32-b%32)+Math.Abs(a/32-b/32)==1;
        public static bool ClearJunctions(HashSet<int> existing,int[] path)
        {
            var combined=new HashSet<int>(existing);combined.UnionWith(path);
            return path.SelectMany(Neighbors).Concat(path).Where(i=>i>=0&&i<1024&&combined.Contains(i)).Distinct()
                .All(i=>Neighbors(i).Count(n=>Adjacent(i,n)&&combined.Contains(n))<=3);
        }
        public int[]? Route(int start,HashSet<int> destination,int limit=40)
        {
            if(!Safe[start]||destination.Count==0)return null;
            int initial=start*5+4;
            var queue=new PriorityQueue<int,int>();var parents=new Dictionary<int,int>{{initial,initial}};
            var costs=new Dictionary<int,int>{{initial,0}};var lengths=new Dictionary<int,int>{{initial,0}};queue.Enqueue(initial,0);
            while(queue.TryDequeue(out int state,out _))
            {
                int id=state/5,direction=state%5;
                if(destination.Contains(id))
                {
                    var route=new List<int>{id};int cursor=state;
                    while(cursor!=initial){cursor=parents[cursor];route.Add(cursor/5);}
                    var result=route.Distinct().ToArray();
                    if(ClearJunctions(destination,result))return result;
                    continue;
                }
                if(lengths[state]>=limit)continue;
                var neighbors=Neighbors(id);
                for(int d=0;d<neighbors.Length;d++)
                {
                    int next=neighbors[d],nextState=next*5+d;
                    if(!Adjacent(id,next)||!Safe[next]||Height[next]!=Height[id])continue;
                    // A portal must enter the district immediately. Running
                    // along an invisible tile boundary creates artificial rails
                    // and a row of apparent unmatched intersections.
                    if((next%32 is 0 or 31||next/32 is 0 or 31)&&next!=start)continue;
                    int cost=costs[state]+10+(direction!=4&&direction!=d?5:0);
                    if(costs.TryGetValue(nextState,out int old)&&old<=cost)continue;
                    costs[nextState]=cost;lengths[nextState]=lengths[state]+1;parents[nextState]=state;
                    queue.Enqueue(nextState,cost*8192+nextState);
                }
            }
            return null;
        }
    }
    private sealed record Bridge(int[] A,int[] B);
    private readonly Dictionary<(int,int),Graph> _graphs=new();
    private readonly Dictionary<(Graph,Graph),Bridge?> _bridges=new();
    private readonly Dictionary<(int,int),Point[]> _tiles=new();
    public int Count=>_tiles.Values.Sum(v=>v.Length);
    public IReadOnlyList<Point> For(RegionPlan region)=>_tiles.GetValueOrDefault((region.Input.X,region.Input.Y),Array.Empty<Point>());

    public RoadNetwork(RegionLayout layout,RoadNetwork? previous=null)
    {
        foreach(var region in layout.Regions)
        {
            var key=(region.Input.X,region.Input.Y);
            _graphs[key]=previous?._graphs.TryGetValue(key,out var old)==true&&ReferenceEquals(old.Region,region)?old:new Graph(layout,region);
        }
        if(previous!=null&&_graphs.Count==previous._graphs.Count&&_graphs.All(p=>previous._graphs.TryGetValue(p.Key,out var graph)&&ReferenceEquals(p.Value,graph)))
        {
            foreach(var pair in previous._tiles)_tiles[pair.Key]=pair.Value;
            foreach(var pair in previous._bridges)_bridges[pair.Key]=pair.Value;
            return;
        }
        var nodes=_graphs.ToDictionary(p=>p.Key,p=>new HashSet<int>(p.Value.Base));
        foreach(var (key,a) in _graphs)
        foreach(var direction in new[]{(X:1,Y:0),(X:0,Y:1)})
        {
            var neighbor=(key.Item1+direction.X,key.Item2+direction.Y);
            if(a.Base.Count==0||a.Region.Input.Owner==""||!_graphs.TryGetValue(neighbor,out var b)||
               b.Base.Count==0||a.Region.Input.Owner!=b.Region.Input.Owner)continue;
            Bridge? bridge=null;
            if(previous?._bridges.TryGetValue((a,b),out var none)==true&&none==null){_bridges[(a,b)]=null;continue;}
            if(previous?._bridges.TryGetValue((a,b),out var cached)==true&&cached!=null&&
               Graph.ClearJunctions(nodes[key],cached.A)&&Graph.ClearJunctions(nodes[neighbor],cached.B))bridge=cached;
            else foreach(int k in Enumerable.Range(1,30).OrderBy(k=>Math.Abs(k-16)))
            {
                int ai=direction.X==1?k*32+31:31*32+k,bi=direction.X==1?k*32:k;
                if(!a.Safe[ai]||!b.Safe[bi]||a.Height[ai]!=b.Height[bi])continue;
                var ar=a.Route(ai,nodes[key]);var br=b.Route(bi,nodes[neighbor]);
                if(ar!=null&&br!=null){bridge=new(ar,br);break;}
            }
            _bridges[(a,b)]=bridge;
            if(bridge!=null){nodes[key].UnionWith(bridge.A);nodes[neighbor].UnionWith(bridge.B);}
        }
        foreach(var (key,values) in nodes)
        {
            var tiles=values.Order().Select(Graph.Point).ToArray();
            _tiles[key]=previous?._tiles.TryGetValue(key,out var old)==true&&old.SequenceEqual(tiles)?old:tiles;
        }
    }
}
