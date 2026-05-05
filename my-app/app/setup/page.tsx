export default function SetupShopPage() {
  return (
    <main className="p-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-semibold">Set up your shop</h1>
      <p className="text-sm text-slate-600 mt-1">Create your shop profile and unique forwarding email.</p>
      <form className="mt-6 grid gap-3" method="post" action="/api/shops">
        <input className="border p-2" name="name" placeholder="Shop Name" />
        <input className="border p-2" name="slug" placeholder="shop-slug" />
        <button className="bg-slate-900 text-white rounded p-2">Create Shop</button>
      </form>
    </main>
  );
}
