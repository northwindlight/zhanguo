using Godot;

public static class PixelTheme
{
    public const string LegacyFontPath="res://assets/fonts/fusion-pixel-12px-monospaced-zh_hans.ttf";
    public const string FontPath="res://assets/fonts/ZLabsRoundPix_16px_M_CN.ttf";
    public static Theme Create(int size=16,string? path=null)
    {
        var font=GD.Load<FontFile>(path??FontPath);
        font.Antialiasing=TextServer.FontAntialiasing.None;
        font.SubpixelPositioning=TextServer.SubpixelPositioning.Disabled;
        font.MultichannelSignedDistanceField=false;font.Oversampling=1;
        if((path??FontPath)!=LegacyFontPath)
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
