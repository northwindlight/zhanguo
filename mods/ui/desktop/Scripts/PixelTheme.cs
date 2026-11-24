using Godot;

public static class PixelTheme
{
    public const string LegacyFontPath="res://assets/fonts/fusion-pixel-12px-monospaced-zh_hans.ttf";
    public const string FontPath="res://assets/fonts/ZLabsRoundPix_16px_M_CN.ttf";
    /// <summary>默认使用漆金皮肤的思源宋体；像素字体保留为设置里的可选项。</summary>
    public static Theme Create(int size=16,string? path=null)
    {
        if((path??UiSkin.BodyFontPath)==UiSkin.BodyFontPath)
        {
            // 宋体字面比像素字体高，账目档位按 0.8 缩放：大字 20→16、标准 16→13。
            var serif=new Theme{DefaultFont=UiSkin.Body,DefaultFontSize=Mathf.RoundToInt(size*.8f)};
            serif.SetConstant("line_spacing","Label",0);serif.SetConstant("line_separation","RichTextLabel",5);
            return serif;
        }
        var font=GD.Load<FontFile>(path);
        font.Antialiasing=TextServer.FontAntialiasing.None;
        font.SubpixelPositioning=TextServer.SubpixelPositioning.Disabled;
        font.MultichannelSignedDistanceField=false;font.Oversampling=1;
        if(path!=LegacyFontPath)
        {
            var fallback=GD.Load<FontFile>(LegacyFontPath);
            fallback.Antialiasing=TextServer.FontAntialiasing.None;
            fallback.SubpixelPositioning=TextServer.SubpixelPositioning.Disabled;
            fallback.MultichannelSignedDistanceField=false;fallback.Oversampling=1;
            font.Fallbacks=new Godot.Collections.Array<Font>{fallback};
        }
        var theme=new Theme{DefaultFont=font,DefaultFontSize=size};
        theme.SetConstant("line_spacing","Label",4);
        theme.SetConstant("line_separation","RichTextLabel",6);
        return theme;
    }
}
