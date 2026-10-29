using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace Zhanguo.Desktop.Layout;

// Exported applications resolve everything beside the executable. Source
// launches retain the existing environment-based developer entry points.
public sealed record DesktopRuntime(string ContentRoot,string DataRoot,string Python,bool Portable)
{
    public static DesktopRuntime Resolve(string executable,string projectRoot,bool exported,string rootOverride="",string pythonOverride="",bool windows=false)
    {
        if(exported)
        {
            string root=Path.GetDirectoryName(Path.GetFullPath(executable))!;
            string marker=Path.Combine(root,"portable.json");
            if(!File.Exists(marker))throw new IOException("便携包不完整：请解压整个 ZIP 后运行 Zhanguo.exe。");
            using var doc=JsonDocument.Parse(File.ReadAllText(marker));
            if(doc.RootElement.GetProperty("format").GetString()!="zhanguo.portable/1")throw new IOException("便携包版本不匹配。");
            string content=Path.Combine(root,"game"),python=Path.Combine(root,"runtime","python",windows?"python.exe":"python3");
            if(!File.Exists(Path.Combine(content,"host.py"))||!File.Exists(python))
                throw new IOException("便携包缺少游戏或 Python：请完整解压，不要只拷贝 exe。");
            return new(content,Path.Combine(root,"userdata"),python,true);
        }
        string source=rootOverride!=""?Path.GetFullPath(rootOverride):Path.GetFullPath(Path.Combine(projectRoot,"../../.."));
        return new(source,source,pythonOverride!=""?pythonOverride:windows?"python":"python3",false);
    }

    public ProcessStartInfo Host(string output,string? config=null,string? resume=null)
    {
        var info=new ProcessStartInfo(Python){WorkingDirectory=ContentRoot,UseShellExecute=false,
            RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true,
            StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        info.Environment["PYTHONIOENCODING"]="utf-8";
        info.Environment["PYTHONDONTWRITEBYTECODE"]="1";
        info.Environment["OPENBLAS_NUM_THREADS"]="1";
        info.Environment["OMP_NUM_THREADS"]="1";
        if(Portable)
        {
            foreach(var key in new[]{"PYTHONHOME","PYTHONPATH","ZHANGUO_ROOT","ZHANGUO_PYTHON","ZHANGUO_DESKTOP_CONFIG","ZHANGUO_RESUME","ZHANGUO_UI_MODS"})
                info.Environment.Remove(key);
            info.Environment["ZHANGUO_UI_MODS"]=Path.Combine(ContentRoot,"ui.toml");
        }
        if(config!=null)info.Environment["ZHANGUO_DESKTOP_CONFIG"]=config;
        if(resume!=null)info.Environment["ZHANGUO_RESUME"]=resume;
        foreach(var arg in new[]{"-X","utf8","-u","-m","ui.desktop","--output",output})info.ArgumentList.Add(arg);
        return info;
    }
}
