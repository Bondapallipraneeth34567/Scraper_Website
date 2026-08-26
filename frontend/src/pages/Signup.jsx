import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { UserPlus } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { useAuth } from '../auth/AuthProvider';

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!name || !email || !password) {
      toast.error('Fill in all fields');
      return;
    }
    if (password !== confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }
    setSubmitting(true);
    try {
      await signup({ name, email });
      toast.success('Account created');
      navigate('/dashboard', { replace: true });
    } catch {
      toast.error('Unable to create account');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Create an account" subtitle="Set up tester access to the verification dashboard">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
        <div>
          <label htmlFor="name" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Full name
          </label>
          <input
            id="name"
            type="text"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Praneeth"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>
        <div>
          <label htmlFor="signup-email" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Email
          </label>
          <input
            id="signup-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>
        <div>
          <label htmlFor="signup-password" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Password
          </label>
          <input
            id="signup-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>
        <div>
          <label htmlFor="confirm-password" className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
            Confirm password
          </label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink outline-none focus:border-good"
          />
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
        >
          <UserPlus size={15} />
          {submitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>

      <p className="mt-5 text-center text-xs text-ink-soft">
        Already have an account?{' '}
        <Link to="/login" className="font-semibold text-good hover:underline">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
