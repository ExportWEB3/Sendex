import { useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../contexts/useAuth';
import { Mail, Lock, User, AlertCircle, Eye, EyeOff, CheckCircle, Key, Loader2 } from 'lucide-react';
import { getErrorMessage } from '../http/api-error';
import { useHttpFetcher } from '../hooks/useHttpFetcher';
import type {
  RegisterRequest,
  RegisterResponse,
  ValidateActivationCodeRequest,
  ValidateActivationCodeResponse,
} from '../../typefiles';

export function Register() {
  const navigate = useNavigate();
  const { isAuthenticated, login } = useAuth();
  const { fetchIt } = useHttpFetcher();
  
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [activationCode, setActivationCode] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [codeValid, setCodeValid] = useState<boolean | null>(null);
  const [codeDuration, setCodeDuration] = useState<number | null>(null);
  const [validatingCode, setValidatingCode] = useState(false);

  // Redirect if already logged in
  if (isAuthenticated) {
    navigate('/');
    return null;
  }

  const validatePassword = () => {
    if (password.length < 8) {
      return 'Password must be at least 8 characters';
    }
    if (password !== confirmPassword) {
      return 'Passwords do not match';
    }
    return null;
  };

  const validateActivationCode = async () => {
    if (activationCode.length < 32) return;
    
    setValidatingCode(true);
    setCodeValid(null);
    
    try {
      const result = await fetchIt<ValidateActivationCodeResponse, ValidateActivationCodeRequest>({
        apiEndPoint: 'auth/validate-code',
        httpMethod: 'post',
        reqData: { code: activationCode },
        auth: 'none',
      });
      if (result.valid) {
        setCodeValid(true);
        setCodeDuration('duration_days' in result ? result.duration_days ?? null : null);
        setError('');
      } else {
        setCodeValid(false);
        setError(result.error || 'Invalid activation code');
      }
    } catch {
      setCodeValid(false);
      setError('Failed to validate activation code');
    }
    
    setValidatingCode(false);
  };

  const handleCodeChange = (value: string) => {
    // Convert to uppercase and remove non-alphanumeric
    const cleaned = value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    setActivationCode(cleaned);
    setCodeValid(null);
    setCodeDuration(null);
    setError('');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    
    if (!activationCode || activationCode.length !== 32) {
      setError('Please enter a valid 32-character activation code');
      return;
    }

    const passwordError = validatePassword();
    if (passwordError) {
      setError(passwordError);
      return;
    }

    setLoading(true);

    try {
      const reqData: RegisterRequest = {
        email,
        password,
        activation_code: activationCode,
        name: name || undefined,
      };
      const result = await fetchIt<RegisterResponse, RegisterRequest>({
        apiEndPoint: 'auth/register',
        httpMethod: 'post',
        reqData,
        auth: 'none',
      });
      
      if (result.success) {
        setSuccess(true);
        // Auto-login after registration
        setTimeout(async () => {
          await login(email, password);
          navigate('/');
        }, 2000);
      } else {
        setError(result.error || 'Registration failed');
      }
    } catch (error: unknown) {
      setError(getErrorMessage(error));
    }
    
    setLoading(false);
  };

  if (success) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-100 via-white to-purple-100 py-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-md w-full">
          <div className="bg-white rounded-2xl shadow-xl p-8 text-center">
            <div className="mx-auto h-16 w-16 bg-green-100 rounded-full flex items-center justify-center mb-6">
              <CheckCircle className="h-8 w-8 text-green-600" />
            </div>
            <h2 className="text-2xl font-bold text-gray-900 mb-2">Account Created!</h2>
            <p className="text-gray-600 mb-4">
              Your account has been successfully created.
            </p>
            {codeDuration && (
              <p className="text-sm text-indigo-600 font-medium mb-6">
                Your account is valid for {codeDuration} days.
              </p>
            )}
            <div className="flex items-center justify-center gap-2 text-gray-500">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span>Logging you in...</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-100 via-white to-purple-100 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full">
        {/* Logo/Title */}
        <div className="text-center mb-8">
          <div className="mx-auto h-16 w-16 bg-indigo-600 rounded-2xl flex items-center justify-center shadow-lg">
            <Mail className="h-8 w-8 text-white" />
          </div>
          <h2 className="mt-6 text-3xl font-bold text-gray-900">Create account</h2>
          <p className="mt-2 text-gray-600">Enter your activation code to get started</p>
        </div>

        {/* Form Card */}
        <div className="bg-white rounded-2xl shadow-xl p-8">
          {error && (
            <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 flex-shrink-0" />
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Activation Code - First and prominent */}
            <div>
              <label htmlFor="activationCode" className="block text-sm font-medium text-gray-700 mb-1">
                Activation Code <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Key className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  id="activationCode"
                  name="activationCode"
                  type="text"
                  required
                  maxLength={32}
                  value={activationCode}
                  onChange={(e) => handleCodeChange(e.target.value)}
                  onBlur={validateActivationCode}
                  className={`block w-full pl-10 pr-10 py-3 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400 font-mono text-sm tracking-wider ${
                    codeValid === true ? 'border-green-500 bg-green-50' : 
                    codeValid === false ? 'border-red-500 bg-red-50' : 
                    'border-gray-300'
                  }`}
                  placeholder="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
                />
                <div className="absolute inset-y-0 right-0 pr-3 flex items-center">
                  {validatingCode && <Loader2 className="h-5 w-5 text-gray-400 animate-spin" />}
                  {!validatingCode && codeValid === true && <CheckCircle className="h-5 w-5 text-green-500" />}
                  {!validatingCode && codeValid === false && <AlertCircle className="h-5 w-5 text-red-500" />}
                </div>
              </div>
              <div className="mt-1 flex justify-between">
                <p className="text-xs text-gray-500">32-character code provided by admin</p>
                <p className="text-xs text-gray-400">{activationCode.length}/32</p>
              </div>
              {codeValid && codeDuration && (
                <p className="mt-1 text-xs text-green-600">
                  ✓ Valid code - Account will be active for {codeDuration} days
                </p>
              )}
            </div>

            <div>
              <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">
                Name <span className="text-gray-400">(optional)</span>
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <User className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  id="name"
                  name="name"
                  type="text"
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                  placeholder="John Doe"
                />
              </div>
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                Email address <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Mail className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                  placeholder="you@example.com"
                />
              </div>
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
                Password <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full pl-10 pr-10 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center"
                >
                  {showPassword ? (
                    <EyeOff className="h-5 w-5 text-gray-400 hover:text-gray-600" />
                  ) : (
                    <Eye className="h-5 w-5 text-gray-400 hover:text-gray-600" />
                  )}
                </button>
              </div>
              <p className="mt-1 text-xs text-gray-500">Must be at least 8 characters</p>
            </div>

            <div>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-1">
                Confirm password <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock className="h-5 w-5 text-gray-400" />
                </div>
                <input
                  id="confirmPassword"
                  name="confirmPassword"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                  placeholder="••••••••"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || !codeValid}
              className="w-full flex justify-center py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? (
                <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              ) : (
                'Create account'
              )}
            </button>
          </form>

          <div className="mt-6 text-center">
            <p className="text-sm text-gray-600">
              Already have an account?{' '}
              <Link to="/login" className="font-medium text-indigo-600 hover:text-indigo-500">
                Sign in
              </Link>
            </p>
          </div>
        </div>

        {/* Info text */}
        <p className="mt-4 text-center text-xs text-gray-500">
          Need an activation code? Contact your administrator.
        </p>
      </div>
    </div>
  );
}
