// CfsDetail.java – Flash-Block der CFS-Firmware sicherstellen, analysieren, "heartbeat"-Strings
// suchen und referenzierende Funktionen dekompilieren.
//
// KORRIGIERT 15.10.2026: Frueher fest "getAddress(0x08000000L)" + createInitializedBlock()
// (zweiter Block neben dem BinaryLoader-Block @0) – FALSCHE Basis. Korrekt:
//   cfs0_* -> 0x08003000, cfs6_* -> 0x08004000 (siehe SetCfsBase.java).
// Basis: Script-Argument (hex, z.B. 0x08003000) oder automatisch aus dem Dateinamen.
// Ist der Flash-Block bereits an der richtigen Stelle (z.B. per SetCfsBase als preScript),
// wird nichts verschoben und nicht erneut analysiert.
//
// Aufruf: analyzeHeadless <projdir> <proj> -process <bin> -postScript CfsDetail.java [0x08003000]

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.util.GhidraProgramUtilities;

public class CfsDetail extends GhidraScript {

    /** identisch zu SetCfsBase.resolveBase() */
    static long resolveBase(String[] args, Program p) {
        if (args != null) {
            for (String a : args) {
                String s = a.trim().toLowerCase();
                if (s.startsWith("0x")) return Long.parseUnsignedLong(s.substring(2), 16);
            }
        }
        String n = (p.getName() + " " + p.getExecutablePath()).toLowerCase();
        if (n.contains("cfs6")) return 0x08004000L;
        if (n.contains("cfs0")) return 0x08003000L;
        return 0x08003000L;
    }

    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        Address base = currentProgram.getAddressFactory().getDefaultAddressSpace()
                .getAddress(resolveBase(getScriptArgs(), currentProgram));

        // 1. Image-Block (groesster initialisierter Block) auf die Basis legen
        MemoryBlock img = null;
        long maxSize = 0;
        for (MemoryBlock b : mem.getBlocks()) {
            if (b.isInitialized() && b.getSize() > maxSize) { maxSize = b.getSize(); img = b; }
        }
        if (img == null) { println("Kein initialisierter Block"); return; }
        boolean moved = false;
        if (!img.getStart().equals(base)) {
            mem.moveBlock(img, base, monitor);
            img = mem.getBlock(base);
            img.setName("flash");
            img.setWrite(false);
            moved = true;
            println("flash-Block verschoben nach " + base + " size=0x" + Long.toHexString(maxSize));
        } else {
            println("flash-Block bereits @" + base);
        }

        // 2. analysieren (nur wenn verschoben oder noch nie analysiert)
        if (moved || !GhidraProgramUtilities.isAnalyzed(currentProgram)) {
            new ghidra.program.flatapi.FlatProgramAPI(currentProgram, monitor).analyzeAll(currentProgram);
        }
        println("Funktionen: " + currentProgram.getFunctionManager().getFunctionCount());

        // 3. "heartbeat" suchen (nur im Flash-Block, uninitialisierte Bloecke ausgenommen)
        byte[] needle = "heartbeat".getBytes("ASCII");
        Address a = img.getStart();
        int hits = 0;
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        while (a != null && hits < 5) {
            a = mem.findBytes(a, img.getEnd(), needle, null, true, monitor);
            if (a == null) break;
            println("STRING heartbeat @ " + a);
            hits++;
            // Stringanfang suchen (Log-Strings beginnen oft mit Farbcodes vor "heartbeat")
            Address st = a;
            while (st.compareTo(img.getStart()) > 0) {
                byte bb = mem.getByte(st.subtract(1));
                if (bb == 0) break;
                st = st.subtract(1);
            }
            int n = 0;
            for (Reference r : getReferencesTo(st)) {
                if (n >= 3) break;
                Function f = getFunctionContaining(r.getFromAddress());
                if (f == null) continue;
                println("  referenziert von Funktion " + f.getEntryPoint());
                DecompileResults dr = di.decompileFunction(f, 60, monitor);
                if (dr != null && dr.getDecompiledFunction() != null) {
                    String code = dr.getDecompiledFunction().getC();
                    println("  --- Pseudocode (" + code.length() + " Zeichen) ---");
                    println(code.length() > 2500 ? code.substring(0, 2500) : code);
                }
                n++;
            }
            a = a.add(1);
        }
        di.dispose();
        if (hits == 0) println("heartbeat NICHT gefunden");
    }
}
