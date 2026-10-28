using System;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace Zhanguo.Desktop.Layout;

// Runs configuration and migration checks in the bundled Python, without a
// session loop, model request or shell command interpolation.
public static class DesktopSetup
{
    private static readonly SemaphoreSlim Gate=new(1,1);
    public static async Task<JsonObject> Call(DesktopRuntime runtime,JsonObject request)
    {
        await Gate.WaitAsync();
        try
        {
            var info=runtime.Host("");info.ArgumentList.Clear();
            foreach(var arg in new[]{"-X","utf8","-u","-m","mods.ui.desktop_setup"})info.ArgumentList.Add(arg);
            using var process=System.Diagnostics.Process.Start(info)??throw new Exception("无法启动配置检查");
            var stdout=process.StandardOutput.ReadToEndAsync();var stderr=process.StandardError.ReadToEndAsync();
            await process.StandardInput.WriteLineAsync(request.ToJsonString());process.StandardInput.Close();
            using var timeout=new CancellationTokenSource(TimeSpan.FromMinutes(5));
            try{await process.WaitForExitAsync(timeout.Token);}
            catch(OperationCanceledException){process.Kill(true);throw new Exception("检查耗时过长，请保留原文件后重试");}
            string body=await stdout;await stderr;
            if(process.ExitCode!=0)throw new Exception("配置检查程序未完成，请查看安装文件是否完整");
            var result=JsonNode.Parse(body)?.AsObject()??throw new Exception("配置检查没有返回结果");
            if(result["ok"]?.GetValue<bool>()!=true)throw new Exception(result["message"]?.ToString()??"配置检查失败");
            return result["data"]!.AsObject();
        }
        finally{Gate.Release();}
    }
}
