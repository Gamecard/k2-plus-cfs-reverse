// SetCfsBase.java – CFS-Firmware (GD32F303VET6, Cortex-M4, RT-Thread 5.0.2) auf die
// korrekte Flash-Basis legen, RAM/Peripherie-Bloecke anlegen, Vektortabelle markieren.
//
// KORRIGIERT 15.10.2026: Frueher fest "0x08000000L" (falsch!). Die App-Images liegen
// hinter einem Bootloader:
//   cfs0_*  (cfs0_000_150/153) -> Basis 0x08003000  (12 KiB Bootloader davor;
//            Versionsstring bei Datei+0x200; 179 Zeiger auf String-Anfaenge vs. 1 bei 0x08000000)
//   cfs6_*  (cfs6_222_000)     -> Basis 0x08004000  (55 vs. 0 Zeiger; Reset 0x08004145)
// Alte Konstante: Address base = ...getAddress(0x08000000L);   // FALSCH, siehe oben
// Alter Reset-Hack: base.add(0x4144) war nur zufaellig richtig fuer cfs6 bei Basis 0.
//
// Aufruf (empfohlen als PRE-Script, damit die Auto-Analyse schon mit richtiger Basis laeuft):
//   analyzeHeadless <projdir> <proj> -import <bin> -loader BinaryLoader \
//     -processor "ARM:LE:32:Cortex" -preScript SetCfsBase.java [0x08003000] \
//     -scriptPath <ghidra-scripts>
// Argument optional: Basis hex. Ohne Argument: Dateiname cfs0* -> 0x08003000, cfs6* -> 0x08004000.
// Laeuft es als POST-Script (Programm bereits analysiert), wird danach analyzeAll() aufgerufen.

import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.data.PointerDataType;
import ghidra.program.flatapi.FlatProgramAPI;
import ghidra.program.util.GhidraProgramUtilities;

public class SetCfsBase extends GhidraScript {

    /** Basis aus Argument oder Dateiname bestimmen (in CfsDetail.java identisch dupliziert). */
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
        return 0x08003000L; // unser Geraet (cfs0_000_153)
    }

    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        long baseL = resolveBase(getScriptArgs(), currentProgram);
        Address base = sp.getAddress(baseL);
        boolean wasAnalyzed = GhidraProgramUtilities.isAnalyzed(currentProgram);
        println("Programm " + currentProgram.getName() + " -> Basis " + base + (wasAnalyzed ? " (bereits analysiert)" : ""));

        // 1. Image-Block finden (BinaryLoader: "ram" @0 bzw. bereits "flash")
        MemoryBlock img = null;
        long max = 0;
        for (MemoryBlock b : mem.getBlocks()) {
            println("Block: " + b.getName() + " " + b.getStart() + " size=0x" + Long.toHexString(b.getSize()));
            if (b.isInitialized() && b.getSize() > max) { max = b.getSize(); img = b; }
        }
        if (img == null) { println("Kein initialisierter Block"); return; }
        if (!img.getStart().equals(base)) {
            mem.moveBlock(img, base, monitor);
            println("Image-Block verschoben nach " + base);
        }
        img = mem.getBlock(base);
        img.setName("flash");
        img.setRead(true); img.setWrite(false); img.setExecute(true);
        long imgSize = img.getSize();

        // 2. Zusaetzliche Bloecke (uninitialisiert), damit Referenzen aufloesen
        mkBlock("bootloader", 0x08000000L, baseL - 0x08000000L, false, true, false);
        long flashEnd = baseL + imgSize;
        mkBlock("flash_rest", flashEnd, 0x08080000L - flashEnd, false, true, false); // 512 KiB Flash
        mkBlock("sram", 0x20000000L, 0x10000L, false, true, true);                    // 64 KiB
        mkBlock("periph", 0x40000000L, 0x10000000L, true, true, true);                // APB/AHB
        mkBlock("periph_ahb3", 0x60000000L, 0x00000100L, true, true, true);           // EXMC-Fenster (klein)
        mkBlock("exmc_regs", 0xA0000000L, 0x1000L, true, true, true);
        mkBlock("ppb", 0xE0000000L, 0x100000L, true, true, true);                     // SCB/NVIC/SysTick

        // 3. Vektortabelle (GD32F303: 16 Core + 68 IRQ = 84 Eintraege)
        int nVec = 84;
        for (int i = 0; i < nVec; i++) {
            Address va = base.add(i * 4L);
            try { createData(va, PointerDataType.dataType); } catch (Exception e) { /* schon da */ }
            if (i == 0) continue; // Initial-SP
            long v = mem.getInt(va) & 0xffffffffL;
            if ((v & 1) == 0 || v < baseL || v >= flashEnd) continue;
            Address ent = sp.getAddress(v & ~1L);
            String name = (i == 1) ? "reset_handler" : String.format("vec_%02d", i);
            try {
                if (getFunctionAt(ent) == null) {
                    disassemble(ent);
                    createFunction(ent, name);
                } else if (i == 1) {
                    getFunctionAt(ent).setName(name, SourceType.USER_DEFINED);
                }
            } catch (Exception e) { println("Vektor " + i + ": " + e.getMessage()); }
        }
        println("SP=0x" + Integer.toHexString(mem.getInt(base)) + " Reset=0x" + Integer.toHexString(mem.getInt(base.add(4))));

        if (wasAnalyzed) {
            new FlatProgramAPI(currentProgram, monitor).analyzeAll(currentProgram);
            println("analyzeAll fertig");
        }
        println("Funktionen: " + currentProgram.getFunctionManager().getFunctionCount());
    }

    private void mkBlock(String name, long start, long len, boolean vol, boolean r, boolean w) {
        if (len <= 0) return;
        Memory mem = currentProgram.getMemory();
        if (mem.getBlock(name) != null) return;
        try {
            Address a = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(start);
            MemoryBlock b = mem.createUninitializedBlock(name, a, len, false);
            b.setVolatile(vol); b.setRead(r); b.setWrite(w); b.setExecute(false);
            println("Block " + name + " @" + a + " len=0x" + Long.toHexString(len));
        } catch (Exception e) {
            println("Block " + name + ": " + e.getMessage());
        }
    }
}
