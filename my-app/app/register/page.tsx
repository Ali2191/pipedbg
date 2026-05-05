export default function RegisterPage() {
  return (
    <main className="p-6 max-w-md mx-auto">
      <h1 className="text-2xl font-semibold">Create account</h1>
      <form className="mt-6 space-y-3" method="post" action="/api/auth/register">
        <input className="w-full border p-2" name="name" placeholder="Name" />
        <input className="w-full border p-2" name="email" placeholder="Email" />
        <input className="w-full border p-2" name="password" placeholder="Password" type="password" />
        <button className="w-full bg-slate-900 text-white p-2 rounded">Create account</button>
      </form>
    </main>
  );
}
