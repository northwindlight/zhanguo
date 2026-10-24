using Godot;

public static class PixelTheme
{
    public const string FontPath="res://assets/fonts/fusion-pixel-12px-monospaced-zh_hans.ttf";
    public static Theme Create(int size=18)
    {
        var font=GD.Load<FontFile>(FontPath);
        font.Antialiasing=TextServer.FontAntialiasing.None;
        font.SubpixelPositioning=TextServer.SubpixelPositioning.Disabled;
        font.MultichannelSignedDistanceField=false;font.Oversampling=1;
        return new Theme{DefaultFont=font,DefaultFontSize=size};
    }
}
