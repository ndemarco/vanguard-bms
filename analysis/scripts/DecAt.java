import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.io.*;

// Usage: -postScript DecAt.java <outfile> <wordaddr hex> [<wordaddr hex> ...]
// Addresses are PROGRAM WORD ADDRESSES as printed in listing.txt ("rom:00a040" -> pass 00a040).
public class DecAt extends GhidraScript {
  public void run() throws Exception {
    DecompInterface d = new DecompInterface();
    d.openProgram(currentProgram);
    try (PrintWriter w = new PrintWriter(new File(getScriptArgs()[0]))) {
      for (int k = 1; k < getScriptArgs().length; k++) {
        Address a = currentProgram.getAddressFactory().getAddress("rom:" + getScriptArgs()[k]);
        Function f = getFunctionAt(a);
        if (f == null) { disassemble(a); f = createFunction(a, null); }
        if (f == null) { w.println("// no function at " + a); continue; }
        DecompileResults r = d.decompileFunction(f, 60, monitor);
        w.println("// ==== " + f.getName() + " @ " + a);
        w.println(r.getDecompiledFunction() == null ? "// fail" : r.getDecompiledFunction().getC());
      }
    }
  }
}
