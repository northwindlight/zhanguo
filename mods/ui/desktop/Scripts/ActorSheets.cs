using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

// Optional character sprite sheets under res://assets/actors/. When a sheet is
// absent the code-drawn figures stay in charge, so Gemini art can arrive later
// file by file without any code change. Layouts come from the manifest beside
// the PNGs; defaults follow the documented reservation spec.
public static class ActorSheets
{
    public sealed record Sheet(Texture2D Texture, int CellW, int CellH, int Frames, int Rows);

    private static readonly Dictionary<string, Sheet?> Cache = new();
    private static JsonElement? _manifest;

    // Row layout every sheet shares: 0 walk right, 1 walk left, 2 attack right,
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
            int cellW = 16, cellH = 16, frames = 6, rows = 2;
            if (Manifest().TryGetProperty(name, out var meta))
            {
                if (meta.TryGetProperty("cell", out var cell) && cell.GetArrayLength() == 2)
                { cellW = cell[0].GetInt32(); cellH = cell[1].GetInt32(); }
                if (meta.TryGetProperty("frames", out var f)) frames = f.GetInt32();
                if (meta.TryGetProperty("rows", out var r)) rows = r.GetInt32();
            }
            if (texture.GetWidth() > 0 && texture.GetHeight() > 0)
            {
                frames = System.Math.Min(frames, texture.GetWidth() / System.Math.Max(1, cellW));
                rows = System.Math.Min(rows, texture.GetHeight() / System.Math.Max(1, cellH));
                if (frames > 0 && rows > 0) sheet = new Sheet(texture, cellW, cellH, frames, rows);
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
