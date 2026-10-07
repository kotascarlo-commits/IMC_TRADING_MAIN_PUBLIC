# Create each owned child suspended, attach it to a kill-on-close Job Object,
# then resume. Descendants inherit the job; no PID-only termination or race.
# Shutdown uses a cooperative shared deadline with checks before release.
# Native API/CloseHandle latency is not a hard wall-time guarantee.
# Member snapshots supplement registered child handles. They are not a proof
# that every historical/transient descendant was captured; callers need their
# own controlled-producer completion evidence before releasing external guards.
if (-not ('IMCRuntimeJob' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public sealed class IMCRuntimeJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long PerProcess, PerJob; public uint Flags; public UIntPtr Min, Max;
        public uint Count; public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IO {
        public ulong ReadCount, WriteCount, OtherCount, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic Basic; public IO IO; public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Startup {
        public uint Size; public string Reserved, Desktop, Title;
        public uint X,Y,Width,Height,CharsX,CharsY,Fill,Flags;
        public ushort Show, ReservedSize; public IntPtr ReservedBytes, In, Out, Error;
    }
    [StructLayout(LayoutKind.Sequential)] struct PI {
        public IntPtr Process, Thread; public uint Id, ThreadId;
    }
    [StructLayout(LayoutKind.Sequential)] struct Security {
        public int Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
    }
    [StructLayout(LayoutKind.Sequential)] struct Accounting {
        public long User, Kernel, PeriodUser, PeriodKernel;
        public uint PageFaults, Total, Active, Terminated;
    }
    sealed class Child {
        public int Id; public IntPtr Process, Thread, Stream;
        public bool Assigned, CloseFault, ExitConfirmed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attrs,string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int type,ref Extended data,uint length);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup startup,out PI info);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateFile(string path,uint access,uint share,ref Security sa,uint disposition,uint flags,IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr handle,uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr handle,out uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int type,out Accounting data,uint length,IntPtr returned);
    [DllImport("kernel32.dll", EntryPoint="QueryInformationJobObject",SetLastError=true)] static extern bool QueryJobProcessIds(IntPtr job,int type,IntPtr data,uint length,IntPtr returned);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint id);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool IsProcessInJob(IntPtr process,IntPtr job,out bool result);
    static readonly List<IMCRuntimeJob> retained = new List<IMCRuntimeJob>();
    IntPtr job; bool closeFault, stopping, captureFault;
    readonly List<Child> children = new List<Child>();
    readonly List<Child> members = new List<Child>();
    readonly HashSet<IntPtr> unconfirmedCloses = new HashSet<IntPtr>();
    public uint? LastActiveCount { get; private set; }
    public bool CleanupConfirmed { get { return job==IntPtr.Zero && children.Count==0 && members.Count==0 && !closeFault && !captureFault; } }
    void Retain() { lock(retained) { if(!retained.Contains(this)) retained.Add(this); } }
    void Close(ref IntPtr handle, Child child) {
        if(handle==IntPtr.Zero) return;
        if(unconfirmedCloses.Contains(handle)) throw new InvalidOperationException("runtime_handle_close_unconfirmed");
        if(!CloseHandle(handle)) {
            unconfirmedCloses.Add(handle);
            closeFault=true; if(child!=null) child.CloseFault=true;
            Retain(); throw new InvalidOperationException("runtime_handle_close_unconfirmed");
        }
        handle=IntPtr.Zero;
    }
    static uint Wait(IntPtr process,uint milliseconds) {
        uint wait=WaitForSingleObject(process,milliseconds);
        if(wait!=0 && wait!=258) throw new Win32Exception();
        return wait;
    }
    Child Find(int id) { return children.Find(delegate(Child child) { return child.Id==id && child.Process!=IntPtr.Zero; }); }
    IEnumerable<Child> Owned() {
        foreach(Child child in children) yield return child;
        foreach(Child child in members) yield return child;
    }
    public void ObserveMember(int id) {
        if(id<=0 || job==IntPtr.Zero || closeFault || captureFault || unconfirmedCloses.Contains(job) || members.Count>=8192)
            throw new InvalidOperationException("runtime_member_observation_invalid");
        Child member=new Child(); member.Id=id; member.Assigned=true; members.Add(member);
        try {
            // No termination rights: member handles are observation/wait only.
            member.Process=OpenProcess(0x101000,false,(uint)id);
            if(member.Process==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            bool belongs;
            if(!IsProcessInJob(member.Process,job,out belongs)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if(!belongs) throw new InvalidOperationException("runtime_member_not_owned");
        } catch {
            captureFault=true; Retain();
            // A failed membership check never authorizes terminating this PID.
            Close(ref member.Process,member); members.Remove(member); throw;
        }
    }
    void CaptureMembers(Stopwatch clock,int milliseconds) {
        for(int capacity=64;capacity<=4096;capacity*=2) {
            Budget(clock,milliseconds);
            int length=8+capacity*IntPtr.Size; IntPtr data=Marshal.AllocHGlobal(length);
            try {
                bool queried=QueryJobProcessIds(job,3,data,(uint)length,IntPtr.Zero);
                int error=queried ? 0 : Marshal.GetLastWin32Error();
                if(!queried && error!=234) throw new Win32Exception(error);
                uint assigned=(uint)Marshal.ReadInt32(data), listed=(uint)Marshal.ReadInt32(data,4);
                if(listed>capacity || listed>assigned) throw new InvalidOperationException("runtime_member_list_invalid");
                if(!queried || listed<assigned) continue;
                for(int i=0;i<listed;i++) {
                    Budget(clock,milliseconds);
                    long id=Marshal.ReadIntPtr(data,8+i*IntPtr.Size).ToInt64();
                    if(id<=0 || id>int.MaxValue) throw new InvalidOperationException("runtime_member_pid_invalid");
                    ObserveMember((int)id);
                }
                return;
            } finally { Marshal.FreeHGlobal(data); }
        }
        throw new InvalidOperationException("runtime_member_list_incomplete");
    }
    public IMCRuntimeJob() {
        job=CreateJobObject(IntPtr.Zero,null);
        if (job==IntPtr.Zero) throw new Win32Exception();
        Extended limits=new Extended(); limits.Basic.Flags=0x2000;
        if (!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(limits))) {
            int error=Marshal.GetLastWin32Error(); Close(ref job,null); throw new Win32Exception(error);
        }
    }
    static string Quote(string value) {
        if (value==null || value.IndexOf('\0')>=0) throw new ArgumentException("invalid_argument");
        StringBuilder b=new StringBuilder("\""); int slashes=0;
        foreach(char c in value) {
            if (c=='\\') { slashes++; continue; }
            if (c=='\"') b.Append('\\',2*slashes+1); else b.Append('\\',slashes);
            b.Append(c); slashes=0;
        }
        b.Append('\\',2*slashes); b.Append('"'); return b.ToString();
    }
    public int Start(string executable,string[] args,string cwd) {
        if (job==IntPtr.Zero) throw new ObjectDisposedException("job");
        if (stopping || closeFault || captureFault) throw new InvalidOperationException("runtime_job_stopping_or_faulted");
        StringBuilder cmd=new StringBuilder(Quote(executable)); foreach(string arg in args) cmd.Append(" "+Quote(arg));
        // Allocate/register cleanup ownership before creating any native child.
        Child child=new Child(); children.Add(child);
        Security security=new Security(); security.Length=Marshal.SizeOf(security); security.Inherit=true;
        try {
            child.Stream=CreateFile("NUL",0xC0000000,3,ref security,3,0,IntPtr.Zero);
            if(child.Stream==new IntPtr(-1)) { child.Stream=IntPtr.Zero; throw new Win32Exception(); }
            Startup startup=new Startup(); startup.Size=(uint)Marshal.SizeOf(startup); startup.Flags=0x100;
            startup.In=startup.Out=startup.Error=child.Stream; PI info=new PI();
            if(!CreateProcess(executable,cmd,IntPtr.Zero,IntPtr.Zero,true,0x08000004,IntPtr.Zero,cwd,ref startup,out info)) throw new Win32Exception();
            child.Process=info.Process; child.Thread=info.Thread; child.Id=(int)info.Id;
            if(!AssignProcessToJobObject(job,child.Process)) throw new Win32Exception();
            child.Assigned=true;
            Close(ref child.Stream,child); // inherited child copy remains valid
            if(ResumeThread(child.Thread)==0xffffffff) throw new Win32Exception();
            Close(ref child.Thread,child);
            return child.Id;
        } catch {
            try {
                if(child.Process!=IntPtr.Zero && Wait(child.Process,0)==258) {
                    if(!TerminateProcess(child.Process,126)) throw new Win32Exception(Marshal.GetLastWin32Error());
                    if(Wait(child.Process,2000)!=0)
                        throw new InvalidOperationException("runtime_start_shutdown_unconfirmed");
                }
                child.ExitConfirmed=child.Process!=IntPtr.Zero;
                if(child.CloseFault) throw new InvalidOperationException("runtime_handle_close_unconfirmed");
                Close(ref child.Thread,child); Close(ref child.Stream,child); Close(ref child.Process,child);
                children.Remove(child);
            } catch { stopping=true; Retain(); throw; }
            throw;
        }
    }
    public bool Running(int id) {
        Child child=Find(id); if(child==null) return false;
        if(unconfirmedCloses.Contains(child.Process)) throw new InvalidOperationException("runtime_handle_close_unconfirmed");
        return Wait(child.Process,0)==258;
    }
    public uint ExitCode(int id) {
        Child child=Find(id); uint code;
        if(child==null || unconfirmedCloses.Contains(child.Process) || !GetExitCodeProcess(child.Process,out code)) throw new Win32Exception();
        return code;
    }
    public void Release(int id) {
        if(stopping || closeFault || captureFault) throw new InvalidOperationException("runtime_job_stopping_or_faulted");
        Child child=Find(id); if(child==null) return;
        if(child.CloseFault) throw new InvalidOperationException("runtime_handle_close_unconfirmed");
        if(Wait(child.Process,0)!=0) throw new InvalidOperationException("cannot_release_running_child");
        child.ExitConfirmed=true;
        Close(ref child.Thread,child); Close(ref child.Stream,child); Close(ref child.Process,child); children.Remove(child);
    }
    public uint ActiveProcessCount() {
        if(job==IntPtr.Zero || unconfirmedCloses.Contains(job)) throw new InvalidOperationException("runtime_job_handle_unavailable");
        Accounting info;
        if(!QueryInformationJobObject(job,1,out info,(uint)Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero)) throw new Win32Exception();
        LastActiveCount=info.Active; return info.Active;
    }
    public void ShutdownWithin(int milliseconds) {
        if(milliseconds<1 || milliseconds>10000) throw new ArgumentOutOfRangeException("milliseconds");
        if(CleanupConfirmed) return;
        stopping=true; Stopwatch clock=Stopwatch.StartNew();
        try {
            bool intactJob=job!=IntPtr.Zero && !unconfirmedCloses.Contains(job);
            if(intactJob && !closeFault && !captureFault) {
                try {CaptureMembers(clock,milliseconds);}
                catch {captureFault=true; Retain();} // cannot prevent best-effort job termination
            }
            if(intactJob && !TerminateJobObject(job,126)) throw new Win32Exception(Marshal.GetLastWin32Error());
            // Failed assignment children are registered but outside the job.
            // A failed close of one child must not prevent stopping the others.
            foreach(Child child in children) {
                Budget(clock,milliseconds);
                if(child.Process!=IntPtr.Zero && !unconfirmedCloses.Contains(child.Process) &&
                    (!child.Assigned || !intactJob) && Wait(child.Process,0)==258 &&
                    !TerminateProcess(child.Process,126)) throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            foreach(Child child in Owned()) {
                Budget(clock,milliseconds);
                if(child.Process!=IntPtr.Zero && !unconfirmedCloses.Contains(child.Process)) {
                    if(Wait(child.Process,0)!=0 && Wait(child.Process,(uint)Math.Max(0,milliseconds-clock.ElapsedMilliseconds))!=0)
                        throw new InvalidOperationException("runtime_process_shutdown_unconfirmed");
                    child.ExitConfirmed=true;
                }
                Budget(clock,milliseconds);
            }
            // Includes descendants not observed in any CIM/process snapshot.
            while(intactJob && ActiveProcessCount()!=0) {
                Budget(clock,milliseconds);
                System.Threading.Thread.Sleep((int)Math.Max(0,Math.Min(10,milliseconds-clock.ElapsedMilliseconds)));
            }
            Budget(clock,milliseconds); // never release after a slow final probe
            if(captureFault) throw new InvalidOperationException("runtime_member_capture_unconfirmed");
            foreach(Child child in Owned()) {
                Budget(clock,milliseconds);
                if(!unconfirmedCloses.Contains(child.Thread)) Close(ref child.Thread,child);
                if(!unconfirmedCloses.Contains(child.Stream)) Close(ref child.Stream,child);
                if(!unconfirmedCloses.Contains(child.Process)) Close(ref child.Process,child);
            }
            if(closeFault) throw new InvalidOperationException("runtime_handle_close_unconfirmed");
            Budget(clock,milliseconds);
            children.Clear(); members.Clear(); Close(ref job,null);
            lock(retained) { retained.Remove(this); }
        } catch { Retain(); throw; }
    }
    static void Budget(Stopwatch clock,int milliseconds) {
        if(clock.ElapsedMilliseconds>=milliseconds) throw new InvalidOperationException("runtime_shutdown_deadline");
    }
    public void Dispose() { ShutdownWithin(10000); }
}
'@
}

# Read-only native mutex identity. No Wait/Release or ownership mutation.
if (-not ('IMCNativeMutexState' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class IMCNativeMutexState {
    [StructLayout(LayoutKind.Sequential)] struct Basic { public int Count; public byte Owned, Abandoned; }
    [StructLayout(LayoutKind.Sequential)] struct Owner { public IntPtr Process, Thread; }
    public sealed class Proof {
        public int Count; public bool Owned, Abandoned;
        public long OwnerProcess, OwnerThread; public uint CallerThread;
    }
    [DllImport("ntdll.dll", EntryPoint="NtQueryMutant")] static extern int QueryBasic(IntPtr handle, int kind, out Basic value, uint bytes, out uint length);
    [DllImport("ntdll.dll", EntryPoint="NtQueryMutant")] static extern int QueryOwner(IntPtr handle, int kind, out Owner value, uint bytes, out uint length);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    public static Proof Read(SafeWaitHandle handle) {
        if (handle.IsClosed || handle.IsInvalid || Marshal.SizeOf(typeof(Basic))!=8 || Marshal.SizeOf(typeof(Owner))!=16)
            throw new InvalidOperationException("runtime_mutex_handle_or_abi_invalid");
        bool pinned=false; handle.DangerousAddRef(ref pinned);
        try {
            if (!pinned) throw new InvalidOperationException("runtime_mutex_handle_unpinned");
            Basic basic; Owner owner; uint length;
            if (QueryBasic(handle.DangerousGetHandle(),0,out basic,8,out length)!=0 || length!=8 || basic.Owned>1 || basic.Abandoned>1)
                throw new InvalidOperationException("runtime_mutex_basic_unconfirmed");
            if (QueryOwner(handle.DangerousGetHandle(),1,out owner,16,out length)!=0 || length!=16)
                throw new InvalidOperationException("runtime_mutex_owner_unconfirmed");
            return new Proof { Count=basic.Count, Owned=basic.Owned==1, Abandoned=basic.Abandoned==1,
                OwnerProcess=owner.Process.ToInt64(), OwnerThread=owner.Thread.ToInt64(), CallerThread=GetCurrentThreadId() };
        } finally { if (pinned) handle.DangerousRelease(); }
    }
}
'@
}
