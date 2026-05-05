import { PDFDocument, StandardFonts, rgb } from "pdf-lib";

export async function buildQuotePdf(quote: any, shop: any) {
  const pdf = await PDFDocument.create();
  const page = pdf.addPage([612, 792]);
  const font = await pdf.embedFont(StandardFonts.Helvetica);
  page.drawText("Thank you for your business", { x: 50, y: 750, size: 20, font, color: rgb(0.1,0.2,0.4) });
  page.drawText(`Quote: ${quote.id}`, { x: 50, y: 720, size: 12, font });
  page.drawText(`Shop: ${shop.name}`, { x: 50, y: 700, size: 12, font });
  page.drawText(`Customer: ${quote.customerName ?? "N/A"}`, { x: 50, y: 680, size: 12, font });
  page.drawText(`Total: $${(quote.totalPrice ?? 0).toFixed(2)}`, { x: 50, y: 660, size: 12, font });
  page.drawText("Terms: Net 30. Quote valid for 30 days.", { x: 50, y: 80, size: 10, font });
  return Buffer.from(await pdf.save());
}
