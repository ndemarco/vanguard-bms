import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import java.io.*;

public class Full2 extends GhidraScript {
  Address W(long wordAddr) {
    return currentProgram.getAddressFactory().getAddress(String.format("rom:%06x", wordAddr));
  }

  public void run() throws Exception {
    Memory m = currentProgram.getMemory();
    // reset vector, IVT and AIVT: two-word entries at word addresses 0..0xFF
    for (long a = 0; a < 0x100; a += 2) {
      disassemble(W(a));
      if (a >= 2) {
        int lo = m.getShort(W(a)) & 0xffff;
        int hi = m.getShort(W(a + 1)) & 0xffff;
        long t = ((long) (hi & 0x7f) << 16) | lo;
        if (t != 0 && m.contains(W(t))) {
          disassemble(W(t));
          createFunction(W(t), null);
        }
      }
    }
    println("RESET " + currentProgram.getListing().getInstructionAt(W(0)));
    analyzeAll(currentProgram);
    println("FUNCS " + currentProgram.getFunctionManager().getFunctionCount());
    String out = getScriptArgs()[0];
    try (PrintWriter w = new PrintWriter(new File(out + "/listing.txt"))) {
      for (Instruction x : currentProgram.getListing().getInstructions(true)) {
        w.println(x.getAddress() + "\t" + x);
      }
    }
    DecompInterface d = new DecompInterface();
    d.openProgram(currentProgram);
    try (PrintWriter w = new PrintWriter(new File(out + "/all.c"))) {
      for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
        DecompileResults res = d.decompileFunction(f, 30, monitor);
        w.println("// ==== " + f.getName() + " @ " + f.getEntryPoint());
        w.println(res.getDecompiledFunction() == null ? "// fail" : res.getDecompiledFunction().getC());
      }
    }
  }
}
