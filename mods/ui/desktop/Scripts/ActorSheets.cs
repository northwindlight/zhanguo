using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

// Character sprite sheets under res://assets/actors/. Custom override sheets
// take priority over the bundled Mini World directional rows. Layouts come
// from the manifest beside the PNGs; missing sheets allow procedural fallback.
public static class ActorSheets
{
    public sealed record Sheet(Texture2D Texture, int CellW, int CellH, int Frames, int Rows,int IdleFrames=1,bool MiniWorld=false,
        int WalkFrames=1,int AttackFrames=1,int[]? IdleRows=null,int[]? WalkRows=null,int[]? AttackRows=null,bool ProceduralAttack=false);

    private static readonly Dictionary<string, Sheet?> Cache = new();
    private static JsonElement? _manifest;

    // Custom override layout: 0 walk right, 1 walk left, 2 attack right,
    // 3 attack left. Rows beyond the sheet's Rows fall back to row 0.
    public static int Row(bool facingRight, bool attacking, int rows)
    {
        int row = !facingRight ? 1 : 0;
        if (attacking && rows >= 4) row += 2;
        return Math.Min(row, rows - 1);
    }

    public static Sheet? Load(string name)
    {
        if (Cache.TryGetValue(name, out var cached)) return cached;
        Sheet? sheet = null;
        string path = $"res://assets/actors/{name}.png";
        if (ResourceLoader.Exists(path))
        {
            var texture = GD.Load<Texture2D>(path);
            int cellW = 16, cellH = 16, frames = 6, rows = 2,idleFrames=1,walkFrames=0,attackFrames=0;bool miniWorld=false,proceduralAttack=false;
            int[]? idleRows=null,walkRows=null,attackRows=null;
            int[]? RowMap(JsonElement meta,string key)=>meta.TryGetProperty(key,out var values)?System.Linq.Enumerable.ToArray(System.Linq.Enumerable.Select(values.EnumerateArray(),v=>v.GetInt32())):null;
            var sheets=Manifest();
            if(sheets.TryGetProperty("sheets",out var nested))sheets=nested;
            if (sheets.TryGetProperty(name, out var meta))
            {
                if (meta.TryGetProperty("cell", out var cell) && cell.GetArrayLength() == 2)
                { cellW = cell[0].GetInt32(); cellH = cell[1].GetInt32(); }
                if (meta.TryGetProperty("frames", out var f)) frames = f.GetInt32();
                if (meta.TryGetProperty("rows", out var r)) rows = r.GetInt32();
                if (meta.TryGetProperty("idle_frames", out var idle)) idleFrames=idle.GetInt32();
                if (meta.TryGetProperty("walk_frames", out var walk)) walkFrames=walk.GetInt32();
                if (meta.TryGetProperty("attack_frames", out var attack)) attackFrames=attack.GetInt32();
                if (meta.TryGetProperty("procedural_attack", out var procedural)) proceduralAttack=procedural.GetBoolean();
                idleRows=RowMap(meta,"idle_rows");walkRows=RowMap(meta,"walk_rows");attackRows=RowMap(meta,"attack_rows");
                if (meta.TryGetProperty("layout",out var layout))miniWorld=layout.GetString()=="mini-world";
            }
            if (texture.GetWidth() > 0 && texture.GetHeight() > 0)
            {
                frames = System.Math.Min(frames, texture.GetWidth() / System.Math.Max(1, cellW));
                rows = System.Math.Min(rows, texture.GetHeight() / System.Math.Max(1, cellH));
                if (frames > 0 && rows > 0) sheet = new Sheet(texture, cellW, cellH, frames, rows,Math.Clamp(idleFrames,1,frames),miniWorld,
                    walkFrames>0?Math.Clamp(walkFrames,1,frames):frames,attackFrames>0?Math.Clamp(attackFrames,1,frames):frames,idleRows,walkRows,attackRows,proceduralAttack);
            }
            if (sheet == null) GD.PrintErr($"素材表 {name}.png 尺寸与清单不符，继续使用代码小人");
        }
        Cache[name] = sheet;
        return sheet;
    }

    private static JsonElement Manifest()
    {
        if (_manifest != null) return _manifest.Value;
        try
        {
            if (Godot.FileAccess.FileExists("res://assets/actors/manifest.json"))
            {
                using var document = JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://assets/actors/manifest.json"));
                _manifest = document.RootElement.Clone();
            }
        }
        catch (System.Exception e) { GD.PrintErr("读取 actors/manifest.json 失败：" + e.Message); }
        _manifest ??= JsonDocument.Parse("{}").RootElement.Clone();
        return _manifest.Value;
    }
}
