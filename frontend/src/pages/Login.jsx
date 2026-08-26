import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { LogIn } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { useAuth } from '../auth/AuthProvider';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const redirectTo = location.state?.from || '/dashboard';

  async function handleSubmit(e) {
    e.preventDefault();
    if (!email || !password) {
      toast.error('Enter your email and password');
      return;
    }
    setSubmitting(true);
    try {
      await login({ email });
      toast.success('Logged in successfully');
      navigate(redirectTo, { replace: true });
    } catch {
      toast.error('Unable to log in');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Log in" subtitle="Access the data verification dashboard">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
        <div>
          <label htmlFor="email" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>
        <div>
          <label htmlFor="password" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
        >
          <LogIn size={15} />
          {submitting ? 'Logging in…' : 'Log in'}
        </button>
      </form>

      <p className="mt-5 text-center text-xs text-ink-soft">
        Don&apos;t have an account?{' '}
        <Link to="/signup" className="font-semibold text-good hover:underline">
          Sign up
        </Link>
      </p>
    </AuthLayout>
  );
}
