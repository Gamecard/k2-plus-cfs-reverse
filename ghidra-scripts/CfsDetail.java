import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.mem.Memory;
import java.util.*;

public class CfsDetail extends GhidraScript {
    @Override
    public void run() throws Exception {
        // 1. flash-Block anlegen
        MemoryBlock target = null;
        long maxSize = 0;
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (b.isInitialized() && b.getSize() > maxSize) { maxSize = b.getSize(); target = b; }
        }
        if (target == null) { println("Kein Block"); return; }
        Address base = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(0x08000000L);
        try {
            List<ghidra.program.database.mem.FileBytes> fbs =
                currentProgram.getMemory().getAllFileBytes();
            if (fbs.isEmpty()) { println("Keine FileBytes"); return; }
            ghidra.program.database.mem.FileBytes fb = fbs.get(0);
            currentProgram.getMemory().createInitializedBlock("flash", base, fb, 0, fb.getSize(), false);
            println("flash-Block: " + base + " size=" + fb.getSize());
        } catch (Exception e) { println("Block-Fehler: " + e.getMessage()); return; }

        // 2. analysieren
        new ghidra.program.flatapi.FlatProgramAPI(currentProgram, monitor).analyzeAll(currentProgram);
        println("Funktionen: " + currentProgram.getFunctionManager().getFunctionCount());

        // 3. "heartbeat" suchen
        Memory mem = currentProgram.getMemory();
        AddressSetView all = new AddressSet(base, base.add(maxSize - 1));
        byte[] needle = "heartbeat".getBytes("ASCII");
        AddressSet found = new AddressSet();
        Address a = base;
        int hits = 0;
        while (a != null && hits < 5) {
            a = mem.findBytes(a, all.getMaxAddress(), needle, null, true, monitor);
            if (a == null) break;
            println("STRING heartbeat @ " + a);
            hits++;
            // Referenzen suchen
            ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(a);
            int n = 0;
            while (ri.hasNext() && n < 3) {
                Reference r = ri.next();
                Function f = currentProgram.getFunctionManager().getFunctionContaining(r.getFromAddress());
                if (f == null) continue;
                println("  referenziert von Funktion " + f.getEntryPoint());
                // dekompilieren
                DecompInterface di = new DecompInterface();
                di.openProgram(currentProgram);
                DecompileResults dr = di.decompileFunction(f, 60, monitor);
                if (dr != null && dr.getDecompiledFunction() != null) {
                    String code = dr.getDecompiledFunction().getC();
                    println("  --- Pseudocode (" + code.length() + " Zeichen) ---");
                    println(code.length() > 2500 ? code.substring(0, 2500) : code);
                }
                di.dispose();
                n++;
            }
            a = a.add(1);
        }
        if (hits == 0) println("heartbeat NICHT gefunden");
    }
}
