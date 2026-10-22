// Temporary numeric pointer-reference locator, scoped to the verified chat buffer.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class PointerRangeProbe
{
    [StructLayout(LayoutKind.Sequential)]
    struct MBI { public IntPtr Base, AllocationBase; public uint AllocationProtect; public ushort PartitionId; public UIntPtr Size; public uint State, Protect, Type; }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint rights, bool inherit, int id);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern UIntPtr VirtualQueryEx(IntPtr process, IntPtr address, out MBI info, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] buffer, UIntPtr length, out UIntPtr read);
    public sealed class Reference { public string Address; public string Value; public long OffsetInBuffer; public string MemoryType; }
    public sealed class Report { public double WallSeconds; public double CpuSeconds; public double ScannedMiB; public bool Complete; public List<Reference> References=new List<Reference>(); }

    public static unsafe List<Reference> Match(byte[] data, int count, long address, ulong low, ulong high)
    {
        if (count < 0 || count > data.Length || low >= high) throw new ArgumentException("Invalid range");
        var found = new List<Reference>();
        fixed (byte* start = data)
        {
            int first = (int)((8 - (address & 7)) & 7);
            for (int offset = first; offset + 8 <= count; offset += 8)
            {
                ulong value = *(ulong*)(start + offset);
                if (value >= low && value < high)
                    found.Add(new Reference { Address="0x"+(address+offset).ToString("x"), Value="0x"+value.ToString("x"), OffsetInBuffer=(long)(value-low) });
            }
        }
        return found;
    }

    public static Report Scan(int id, ulong low, ulong high, long markerAddress, string marker, ulong extraTarget)
    {
        var game = Process.GetProcessById(id);
        if (!game.ProcessName.Equals("HeroesOfTheStorm_x64", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Wrong process");
        IntPtr handle = OpenProcess(0x410, false, id);
        if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        try
        {
            byte[] expected = Encoding.UTF8.GetBytes(marker);
            byte[] check = new byte[expected.Length]; UIntPtr count;
            if (!ReadProcessMemory(handle, new IntPtr(markerAddress), check, (UIntPtr)check.Length, out count) || count.ToUInt64()!=(ulong)expected.Length)
                throw new Exception("Known marker no longer readable");
            for(int i=0;i<check.Length;i++) if(check[i]!=expected[i]) throw new Exception("Known marker changed; stop");
            var result = new Report();
            var clock = Stopwatch.StartNew();
            var host = Process.GetCurrentProcess();
            host.PriorityClass = ProcessPriorityClass.BelowNormal;
            double cpu = host.TotalProcessorTime.TotalSeconds;
            long address = 0, bytes = 0;
            byte[] buffer = new byte[1024*1024];
            while(address < 0x7fffffff0000L)
            {
                MBI info;
                if(VirtualQueryEx(handle,new IntPtr(address),out info,(UIntPtr)Marshal.SizeOf(typeof(MBI)))==UIntPtr.Zero) { result.Complete=true; break; }
                long start=info.Base.ToInt64(), size=(long)info.Size.ToUInt64();
                if(size<=0 || start+size<=address) throw new Exception("Invalid region");
                uint protection=info.Protect&0xff;
                bool readable=protection==2 || protection==4 || protection==8;
                if(info.State==0x1000 && (info.Type==0x20000 || info.Type==0x1000000) && readable && (info.Protect&0x100)==0)
                {
                    for(long offset=0;offset<size;offset+=buffer.Length)
                    {
                        int wanted=(int)Math.Min(buffer.Length,size-offset);
                        ReadProcessMemory(handle,new IntPtr(start+offset),buffer,(UIntPtr)wanted,out count);
                        int length=(int)count.ToUInt64(); bytes+=length;
                        foreach(var reference in Match(buffer,length,start+offset,low,high))
                        {
                            reference.MemoryType=info.Type==0x20000?"private":"image";
                            result.References.Add(reference);
                        }
                        if(extraTarget!=0)
                            foreach(var reference in Match(buffer,length,start+offset,extraTarget,extraTarget+1))
                            {
                                reference.MemoryType=info.Type==0x20000?"private":"image";
                                result.References.Add(reference);
                            }
                        double targetMs=bytes/(64.0*1024*1024)*1000;
                        if(clock.Elapsed.TotalMilliseconds<targetMs) Thread.Sleep((int)(targetMs-clock.Elapsed.TotalMilliseconds));
                        if(clock.Elapsed.TotalSeconds>90) break;
                    }
                }
                address=start+size;
                if(clock.Elapsed.TotalSeconds>90) break;
            }
            if(address>=0x7fffffff0000L) result.Complete=true;
            host.Refresh(); result.WallSeconds=clock.Elapsed.TotalSeconds; result.CpuSeconds=host.TotalProcessorTime.TotalSeconds-cpu; result.ScannedMiB=bytes/(1024.0*1024);
            return result;
        }
        finally { CloseHandle(handle); }
    }
}
