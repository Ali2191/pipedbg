import * as XLSX from "xlsx";

export function buildQuoteExcel(quote: any, shop: any) {
  const rows = [
    ["Quote ID", quote.id],
    ["Shop", shop.name],
    ["Customer", quote.customerName ?? ""],
    ["Project", quote.projectName ?? ""],
    ["Material Cost", quote.materialCost ?? 0],
    ["Labor Cost", quote.laborCost ?? 0],
    ["Overhead", quote.overheadCost ?? 0],
    ["Total", quote.totalPrice ?? 0],
  ];
  const ws = XLSX.utils.aoa_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Quote");
  return XLSX.write(wb, { type: "buffer", bookType: "xlsx" });
}
