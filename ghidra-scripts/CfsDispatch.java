// CfsDispatch.java – RS485-Kommando-Dispatcher der CFS-Firmware finden und auflisten (15.10.2026).
//
// Voraussetzung: Programm mit korrekter Basis importiert (SetCfsBase.java als preScript).
// Ansatz: Alle Handler rufen eine Laengenpruefung lencheck(fun, len, erwartete_len) auf
//   (cfs0_000_153: FUN_0800ebc2, cfs6_222_000: FUN_0800e0ec). Die Funktion mit den meisten
//   Aufrufen der Form "movs r0,#fun ... movs r2,#len ... bl X" ist lencheck. Jede Aufrufstelle
//   liefert fun-Code + erwartetes Laengenbyte (= 3 + Anzahl Datenbytes).
// Zusaetzlich: TBB/TBH-Sprungtabellen in der Dispatcher-Funktion reparieren (Ghidra
//   disassembliert die Tabellenbytes sonst als Code -> "halt_baddata") und JumpTable-Override
//   setzen; danach Dekompilat schreiben.
//
// Aufruf: analyzeHeadless <projdir> <proj> -process <bin> -postScript CfsDispatch.java \
//           [out=/pfad/dispatcher.c] [lencheck=0x0800ebc2] [nofix]
// (ohne -readOnly, wenn die TBB-Reparatur gespeichert werden soll)

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.pcode.JumpTable;
import ghidra.app.decompiler.*;
import java.io.*;
import java.util.*;

public class CfsDispatch extends GhidraScript {

    static class Site { Address call; long fun; long len; Function in; List<Address> next = new ArrayList<>(); }

    @Override
    public void run() throws Exception {
        String out = null; Address lc = null; boolean fix = true;
        for (String a : getScriptArgs()) {
            if (a.startsWith("out=")) out = a.substring(4);
            else if (a.startsWith("lencheck=")) lc = toAddr(Long.parseUnsignedLong(a.substring(9).replace("0x", ""), 16));
            else if (a.equals("nofix")) fix = false;
        }

        // Pass 1: scannen, Dispatcher bestimmen, TBB reparieren; Pass 2: erneut scannen
        Object[] r1 = scan(lc);
        if (r1 == null) { println("lencheck nicht gefunden"); return; }
        lc = (Address) r1[0];
        Function disp = (Function) r1[2];
        if (disp != null && fix) {
            Address st = disp.getEntryPoint(), en = disp.getBody().getMaxAddress();
            fixTbb(st, en, disp.getName());
            disp = getFunctionAt(st);
            Object[] r2 = scan(lc);
            if (r2 != null) r1 = r2;
        }
        @SuppressWarnings("unchecked") List<Site> sites = (List<Site>) r1[1];
        println("lencheck = " + lc + " (" + sites.size() + " Aufrufstellen)");
        println("Dispatcher: " + (disp == null ? "-" : disp.getEntryPoint().toString()));
        StringBuilder tab = new StringBuilder();
        tab.append(String.format("%-6s %-8s %-6s %-12s %-12s %s%n", "fun", "len", "ndata", "call", "in", "folgeaufrufe"));
        for (Site s : sites) {
            StringBuilder nx = new StringBuilder();
            for (Address a : s.next) { Function f = getFunctionAt(a); nx.append(a).append(f != null ? "(" + f.getName() + ") " : " "); }
            tab.append(String.format("0x%02x   0x%02x     %-6d %-12s %-12s %s%n", s.fun, s.len, s.len - 3, s.call,
                    s.in == null ? "-" : s.in.getEntryPoint().toString(), nx));
        }
        println("\n" + tab);

        // 3. Dekompilat
        if (disp != null && out != null) {
            DecompInterface di = new DecompInterface();
            di.openProgram(currentProgram);
            DecompileResults r = di.decompileFunction(disp, 180, monitor);
            try (PrintWriter w = new PrintWriter(new FileWriter(out))) {
                w.println("// CfsDispatch.java " + currentProgram.getName() + " lencheck=" + lc);
                w.println("/*\n" + tab + "*/");
                w.println(r.getDecompiledFunction() != null ? r.getDecompiledFunction().getC() : "<decompile failed>");
            }
            di.dispose();
            println("Dekompilat -> " + out);
        }
    }

    Object[] scan(Address lc) throws Exception {
        Listing L = currentProgram.getListing();
        Map<Address, List<Site>> byTarget = new HashMap<>();
        for (Instruction in : L.getInstructions(true)) {
            if (!in.getMnemonicString().equalsIgnoreCase("bl")) continue;
            Address[] fl = in.getFlows();
            if (fl.length != 1) continue;
            Long r0 = null, r2 = null;
            Instruction p = in.getPrevious();
            for (int i = 0; i < 6 && p != null; i++, p = p.getPrevious()) {
                String m = p.getMnemonicString().toLowerCase();
                if (m.startsWith("b") && !m.startsWith("bic")) break;
                if (m.startsWith("mov") && p.getNumOperands() == 2) {
                    Object[] o0 = p.getOpObjects(0), o1 = p.getOpObjects(1);
                    if (o0.length == 1 && o1.length == 1 && o1[0] instanceof Scalar) {
                        String reg = o0[0].toString();
                        long v = ((Scalar) o1[0]).getUnsignedValue();
                        if (reg.equals("r0") && r0 == null) r0 = v;
                        if (reg.equals("r2") && r2 == null) r2 = v;
                    }
                }
            }
            if (r0 == null || r2 == null) continue;
            Site s = new Site(); s.call = in.getAddress(); s.fun = r0; s.len = r2;
            s.in = getFunctionContaining(in.getAddress());
            Instruction q = in.getNext();
            for (int i = 0; i < 14 && q != null && s.next.size() < 2; i++, q = q.getNext()) {
                if (q.getMnemonicString().equalsIgnoreCase("bl") && q.getFlows().length == 1) s.next.add(q.getFlows()[0]);
            }
            byTarget.computeIfAbsent(fl[0], k -> new ArrayList<>()).add(s);
        }
        if (lc == null) {
            int best = 0;
            for (Map.Entry<Address, List<Site>> e : byTarget.entrySet()) {
                // lencheck: fun-Codes muessen ueberwiegend verschieden sein
                Set<Long> funs = new HashSet<>();
                for (Site s : e.getValue()) funs.add(s.fun);
                if (funs.size() > best) { best = funs.size(); lc = e.getKey(); }
            }
        }
        if (lc == null || !byTarget.containsKey(lc)) return null;
        List<Site> sites = byTarget.get(lc);
        sites.sort(Comparator.comparingLong(s -> s.fun));

        // Dispatcher = Funktion mit den meisten Aufrufstellen (cfs0: alles inline in einer Funktion);
        // liegt jede Pruefung in einem eigenen Handler (cfs6), dann der haeufigste gemeinsame Aufrufer.
        Map<Function, Integer> cnt = new HashMap<>();
        for (Site s : sites) if (s.in != null) cnt.merge(s.in, 1, Integer::sum);
        Function disp = null; int mx = 0;
        for (Map.Entry<Function, Integer> e : cnt.entrySet()) if (e.getValue() > mx) { mx = e.getValue(); disp = e.getKey(); }
        if (mx <= 2) {
            Map<Function, Integer> cc = new HashMap<>();
            for (Function h : cnt.keySet()) for (Function c : h.getCallingFunctions(monitor)) cc.merge(c, 1, Integer::sum);
            mx = 0;
            for (Map.Entry<Function, Integer> e : cc.entrySet()) if (e.getValue() > mx) { mx = e.getValue(); disp = e.getKey(); }
        }
        return new Object[] { lc, sites, disp };
    }


    /** TBB/TBH-Tabellen in [st,en] als Daten markieren, COMPUTED_JUMP-Referenzen + JumpTable-Override setzen. */
    void fixTbb(Address st, Address en, String name) throws Exception {
        List<Instruction> tbbs = new ArrayList<>();
        for (Instruction in : currentProgram.getListing().getInstructions(new AddressSet(st, en), true)) {
            String m = in.getMnemonicString().toLowerCase();
            if (m.equals("tbb") || m.equals("tbh")) tbbs.add(in);
        }
        if (tbbs.isEmpty()) { println("keine TBB/TBH im Dispatcher"); return; }
        Map<Address, List<Address>> tables = new LinkedHashMap<>();
        Map<Address, Integer> sizes = new HashMap<>();
        Map<Address, Boolean> half = new HashMap<>();
        for (Instruction in : tbbs) {
            int n = -1;
            Instruction p = in.getPrevious();
            for (int k = 0; k < 4 && p != null; k++, p = p.getPrevious()) {
                if (p.getMnemonicString().toLowerCase().startsWith("cmp")) {
                    Object[] o = p.getOpObjects(1);
                    if (o.length > 0 && o[0] instanceof Scalar) { n = (int) ((Scalar) o[0]).getValue(); break; }
                }
            }
            if (n <= 0) { println("TBB @" + in.getAddress() + ": Tabellenlaenge unbekannt"); continue; }
            boolean h = in.getMnemonicString().equalsIgnoreCase("tbh");
            Address tb = in.getAddress().add(4);
            List<Address> tl = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                int off = h ? (currentProgram.getMemory().getShort(tb.add(2L * i)) & 0xffff)
                            : (currentProgram.getMemory().getByte(tb.add(i)) & 0xff);
                tl.add(tb.add(2L * off));
            }
            tables.put(in.getAddress(), tl); sizes.put(in.getAddress(), n); half.put(in.getAddress(), h);
        }
        Function f = getFunctionAt(st);
        if (f != null) removeFunction(f);
        clearListing(st, en);
        for (Map.Entry<Address, List<Address>> e : tables.entrySet()) {
            Address tb = e.getKey().add(4);
            int n = sizes.get(e.getKey()); boolean h = half.get(e.getKey());
            clearListing(tb, tb.add((h ? 2L : 1L) * n - 1));
            for (int i = 0; i < n; i++) { if (h) createWord(tb.add(2L * i)); else createByte(tb.add(i)); }
        }
        disassemble(st);
        for (Map.Entry<Address, List<Address>> e : tables.entrySet()) {
            if (getInstructionAt(e.getKey()) == null) disassemble(e.getKey());
            for (Address t : e.getValue()) {
                currentProgram.getReferenceManager().addMemoryReference(e.getKey(), t, RefType.COMPUTED_JUMP, SourceType.USER_DEFINED, -1);
                if (getInstructionAt(t) == null) disassemble(t);
            }
        }
        Function nf = createFunction(st, name);
        if (nf == null) { println("Funktion konnte nicht neu angelegt werden"); return; }
        for (Map.Entry<Address, List<Address>> e : tables.entrySet()) {
            new JumpTable(e.getKey(), new ArrayList<>(new LinkedHashSet<>(e.getValue())), true, 0).writeOverride(nf);
            println("TBB @" + e.getKey() + " n=" + e.getValue().size() + " repariert");
        }
    }
}
