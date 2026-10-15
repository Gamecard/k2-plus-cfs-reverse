import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.mem.MemoryBlockException;
import ghidra.program.model.address.Address;
import ghidra.program.flatapi.FlatProgramAPI;

public class SetCfsBase extends GhidraScript {
    @Override
    public void run() throws Exception {
        MemoryBlock target = null;
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            println("Block: " + b.getName() + " " + b.getStart() + " size=" + b.getSize());
            if (b.getName().endsWith(".bin")) { target = b; }
        }
        if (target == null) {
            // BinaryLoader nennt den Block wie die Datei; Fallback: größter initialisierter Block
            long maxSize = 0;
            for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
                if (b.isInitialized() && b.getSize() > maxSize) { maxSize = b.getSize(); target = b; }
            }
        }
        if (target == null) { println("Kein Block gefunden"); return; }
        println("Block vorher: " + target.getName() + " " + target.getStart() + " size=" + target.getSize());
        Address base = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(0x08000000L);
        try {
            ghidra.program.database.mem.FileBytes fb = null;
            for (ghidra.program.database.mem.FileBytes f : currentProgram.getMemory().getAllFileBytes()) {
                if (f.getFilename().endsWith(".bin")) { fb = f; break; }
            }
            if (fb == null) fb = currentProgram.getMemory().getAllFileBytes().get(0);
            currentProgram.getMemory().createInitializedBlock("flash", base, fb, 0, fb.getSize(), false);
            println("flash-Block angelegt an " + base + " size=" + fb.getSize());
        } catch (Exception e) {
            println("createBlock Fehler: " + e.getMessage());
        }
        // Reset-Vektor (SP=0x20008030, Reset=0x08004144) als Funktion markieren
        Address reset = base.add(0x4144);
        try {
            currentProgram.getListing().createFunction("reset_handler", reset, null, null);
            println("reset_handler angelegt an " + reset);
        } catch (Exception e) {
            println("reset_handler: " + e.getMessage());
        }
        FlatProgramAPI api = new FlatProgramAPI(currentProgram, monitor);
        api.analyzeAll(currentProgram);
        println("analyzeAll fertig, Funktionen: " + currentProgram.getFunctionManager().getFunctionCount());
    }
}
