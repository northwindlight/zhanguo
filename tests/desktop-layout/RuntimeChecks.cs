using System;
using System.IO;
using System.Linq;
using Zhanguo.Desktop.Layout;

static class RuntimeChecks
{
    static void Require(bool value,string why){if(!value)throw new Exception(why);}
    public static void Run()
    {
        string root=Path.Combine(Path.GetTempPath(),"战国 portable "+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root,"game"));Directory.CreateDirectory(Path.Combine(root,"runtime","python"));
        try
        {
            File.WriteAllText(Path.Combine(root,"portable.json"),"{\"format\":\"zhanguo.portable/1\"}");
            File.WriteAllText(Path.Combine(root,"game","host.py"),"");File.WriteAllText(Path.Combine(root,"runtime","python","python.exe"),"");
            var paths=DesktopRuntime.Resolve(Path.Combine(root,"Zhanguo.exe"),"unavailable",true,"unavailable source","unavailable python",true);
            Require(paths.ContentRoot==Path.Combine(root,"game")&&paths.DataRoot==Path.Combine(root,"userdata"),"export used source paths or mixed code with user data");
            Require(paths.Python==Path.Combine(root,"runtime","python","python.exe"),"export used installed Python");
            string? previous=Environment.GetEnvironmentVariable("ZHANGUO_DESKTOP_CONFIG");
            try
            {
                Environment.SetEnvironmentVariable("ZHANGUO_DESKTOP_CONFIG","unwanted private config");
                var host=paths.Host(Path.Combine(paths.DataRoot,"save with spaces"));
                Require(!host.Environment.ContainsKey("ZHANGUO_DESKTOP_CONFIG"),"portable default inherited a private model config");
                Require(host.WorkingDirectory==paths.ContentRoot&&host.ArgumentList.Last().EndsWith("save with spaces"),"host lost a Unicode/space-containing path");
                Require(paths.Host("output","explicit config").Environment["ZHANGUO_DESKTOP_CONFIG"]=="explicit config","explicit menu config was lost");
            }
            finally{Environment.SetEnvironmentVariable("ZHANGUO_DESKTOP_CONFIG",previous);}
            var source=DesktopRuntime.Resolve("unused",Path.Combine(root,"mods/ui/desktop"),false,root,"custom-python");
            Require(!source.Portable&&source.DataRoot==root&&source.Python=="custom-python","source launcher overrides changed");
            File.Delete(Path.Combine(root,"runtime/python/python.exe"));
            bool rejected=false;try{DesktopRuntime.Resolve(Path.Combine(root,"Zhanguo.exe"),"unused",true,windows:true);}catch(IOException){rejected=true;}
            Require(rejected,"incomplete exports silently fell back to a system installation");
            Console.WriteLine("PORTABLE_RUNTIME_OK: relocated Unicode paths, bundled Python, isolated defaults and preserved source entry");
        }
        finally{Directory.Delete(root,true);}
    }
}
