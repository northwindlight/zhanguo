"""Path planning on observed tiles only; unknown terrain is not inspected."""
import heapq

def chebyshev(a,b):return max(abs(a[0]-b[0]),abs(a[1]-b[1]))

def distance(knowledge, unit, start, goal):
    cache=knowledge.setdefault('_cost_fields',{})
    key=(unit,tuple(goal))
    if key not in cache:
        if '_tiles' not in knowledge:knowledge['_tiles']={tuple(t['position']):t for t in knowledge['view']}
        tiles=knowledge['_tiles']
        costs=knowledge['unit_catalog'][unit]['move_cost']
        queue=[(0,goal)];best={goal:0}
        while queue:
            cost,point=heapq.heappop(queue)
            if cost!=best[point] or point not in tiles:continue
            if point!=goal and tiles[point]['owner'] is not None and tiles[point]['owner'] not in knowledge['allies']:continue
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    if not (dx or dy):continue
                    target=(point[0]+dx,point[1]+dy)
                    if target not in tiles:continue
                    step=max(costs[tiles[point]['terrain']],costs[tiles[target]['terrain']])
                    total=cost+step
                    if total<best.get(target,float('inf')):best[target]=total;heapq.heappush(queue,(total,target))
        cache[key]=best
    return cache[key].get(tuple(start),1000+chebyshev(start,goal))
