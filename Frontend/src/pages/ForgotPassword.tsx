import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { PasswordErrors, Step } from '../../typefiles';
import { Mail, Lock, ShieldCheck, AlertCircle, CheckCircle, Eye, EyeOff, ArrowLeft, RefreshCw } from 'lucide-react';
import { getErrorMessage } from '../http/api-error';
import { useHttpFetcher } from '../hooks/useHttpFetcher';
import type { CaptchaResponse, ForgotPasswordCaptchaRequest, PasswordResetRequest, PasswordResetResponse } from '../../typefiles';

function validatePassword(password: string, confirmPassword: string): PasswordErrors {
  return {
    length: password.length >= 8,
    uppercase: /[A-Z]/.test(password),
    lowercase: /[a-z]/.test(password),
    number: /[0-9]/.test(password),
    match: password.length > 0 && password === confirmPassword,
  };
}

function PasswordCriteria({ label, met }: { label: string; met: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-xs ${met ? 'text-green-600' : 'text-gray-400'}`}>
      {met ? (
        <CheckCircle className="h-3.5 w-3.5" />
      ) : (
        <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />
      )}
      {label}
    </div>
  );
}

export function ForgotPassword() {
  const { fetchIt } = useHttpFetcher();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [captchaId, setCaptchaId] = useState('');
  const [captchaQuestion, setCaptchaQuestion] = useState('');
  const [captchaAnswer, setCaptchaAnswer] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');

  const passwordCheck = validatePassword(newPassword, confirmPassword);
  const allPasswordCriteriaMet =
    passwordCheck.length && passwordCheck.uppercase && passwordCheck.lowercase && passwordCheck.number && passwordCheck.match;

  // Step 1: Submit email and request a verification code
  const handleEmailSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data = await fetchIt<CaptchaResponse, ForgotPasswordCaptchaRequest>({
        apiEndPoint: 'auth/forgot-password/captcha',
        httpMethod: 'post',
        reqData: { email },
        auth: 'none',
      });

      setCaptchaId(data.captcha_id);
      setCaptchaQuestion(data.captcha_question);
      setStep('captcha');
    } catch (error: unknown) {
      setError(getErrorMessage(error));
    }
    setLoading(false);
  };

  // Request a new verification code
  const handleRefreshCaptcha = async () => {
    setError('');
    setCaptchaAnswer('');
    setLoading(true);
    try {
      const data = await fetchIt<CaptchaResponse, ForgotPasswordCaptchaRequest>({
        apiEndPoint: 'auth/forgot-password/captcha',
        httpMethod: 'post',
        reqData: { email },
        auth: 'none',
      });

      setCaptchaId(data.captcha_id);
      setCaptchaQuestion(data.captcha_question);
    } catch (error: unknown) {
      setError(getErrorMessage(error));
    }
    setLoading(false);
  };

  // Step 2: Submit verification code + new password
  const handleResetSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');

    if (!allPasswordCriteriaMet) {
      setError('Please meet all password requirements');
      return;
    }

    setLoading(true);
    try {
      const reqData: PasswordResetRequest = {
        email,
        captcha_id: captchaId,
        captcha_answer: captchaAnswer,
        new_password: newPassword,
        confirm_password: confirmPassword,
      };
      const data = await fetchIt<PasswordResetResponse, PasswordResetRequest>({
        apiEndPoint: 'auth/forgot-password/reset',
        httpMethod: 'post',
        reqData,
        auth: 'none',
      });

      setSuccessMessage(data.message || 'Password reset successfully!');
      setStep('success');
    } catch (error: unknown) {
      const message = getErrorMessage(error);
      setError(message);
      if (message.includes('expired') || message.includes('new captcha') || message.includes('new code')) {
        setCaptchaId('');
        setCaptchaQuestion('');
        setCaptchaAnswer('');
      }
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-100 via-white to-purple-100 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full">
        {/* Logo/Title */}
        <div className="text-center mb-8">
          <div className="mx-auto h-16 w-16 bg-indigo-600 rounded-2xl flex items-center justify-center shadow-lg">
            <ShieldCheck className="h-8 w-8 text-white" />
          </div>
          <h2 className="mt-6 text-3xl font-bold text-gray-900">
            {step === 'success' ? 'Password Reset!' : 'Reset Password'}
          </h2>
          <p className="mt-2 text-gray-600">
            {step === 'email' && 'Enter your email to get started'}
            {step === 'captcha' && 'Verify your email and set your new password'}
            {step === 'success' && 'You can now sign in with your new password'}
          </p>
        </div>

        {/* Form Card */}
        <div className="bg-white rounded-2xl shadow-xl p-8">
          {error && (
            <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 flex-shrink-0" />
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          )}

          {/* STEP 1: Email */}
          {step === 'email' && (
            <form onSubmit={handleEmailSubmit} className="space-y-6">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email address
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

              <button
                type="submit"
                disabled={loading}
                className="w-full flex justify-center py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? (
                  <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  'Continue'
                )}
              </button>
            </form>
          )}

          {/* STEP 2: Verification code + New Password */}
          {step === 'captcha' && (
            <form onSubmit={handleResetSubmit} className="space-y-5">
              {/* Email verification challenge */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Security Check
                </label>
                <div className="bg-gradient-to-r from-gray-800 to-gray-900 rounded-lg p-4 flex items-center justify-between mb-2">
                  <span className="text-2xl font-mono font-bold text-green-400 tracking-wider select-none">
                    {captchaQuestion}
                  </span>
                  <button
                    type="button"
                    onClick={handleRefreshCaptcha}
                    disabled={loading}
                    className="text-gray-400 hover:text-white transition-colors p-1"
                    title="Send a new verification code"
                  >
                    <RefreshCw className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} />
                  </button>
                </div>
                <input
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  required
                  value={captchaAnswer}
                  onChange={(e) => setCaptchaAnswer(e.target.value)}
                  className="block w-full px-3 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                  placeholder="Enter the 6-digit code"
                  autoComplete="one-time-code"
                />
              </div>

              {/* New Password */}
              <div>
                <label htmlFor="new-password" className="block text-sm font-medium text-gray-700 mb-1">
                  New Password
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Lock className="h-5 w-5 text-gray-400" />
                  </div>
                  <input
                    id="new-password"
                    type={showPassword ? 'text' : 'password'}
                    required
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
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
              </div>

              {/* Confirm Password */}
              <div>
                <label htmlFor="confirm-password" className="block text-sm font-medium text-gray-700 mb-1">
                  Confirm Password
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Lock className="h-5 w-5 text-gray-400" />
                  </div>
                  <input
                    id="confirm-password"
                    type={showConfirmPassword ? 'text' : 'password'}
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="block w-full pl-10 pr-10 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-gray-900 placeholder-gray-400"
                    placeholder="••••••••"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                    className="absolute inset-y-0 right-0 pr-3 flex items-center"
                  >
                    {showConfirmPassword ? (
                      <EyeOff className="h-5 w-5 text-gray-400 hover:text-gray-600" />
                    ) : (
                      <Eye className="h-5 w-5 text-gray-400 hover:text-gray-600" />
                    )}
                  </button>
                </div>
              </div>

              {/* Password Criteria */}
              {newPassword.length > 0 && (
                <div className="bg-gray-50 rounded-lg p-3 space-y-1.5">
                  <PasswordCriteria label="At least 8 characters" met={passwordCheck.length} />
                  <PasswordCriteria label="One uppercase letter" met={passwordCheck.uppercase} />
                  <PasswordCriteria label="One lowercase letter" met={passwordCheck.lowercase} />
                  <PasswordCriteria label="One number" met={passwordCheck.number} />
                  <PasswordCriteria label="Passwords match" met={passwordCheck.match} />
                </div>
              )}

              <button
                type="submit"
                disabled={loading || !allPasswordCriteriaMet}
                className="w-full flex justify-center py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? (
                  <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  'Reset Password'
                )}
              </button>

              {/* Back button */}
              <button
                type="button"
                onClick={() => {
                  setStep('email');
                  setCaptchaId('');
                  setCaptchaQuestion('');
                  setCaptchaAnswer('');
                  setNewPassword('');
                  setConfirmPassword('');
                  setError('');
                }}
                className="w-full flex items-center justify-center gap-2 py-2 text-sm text-gray-500 hover:text-gray-700 transition-colors"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to email
              </button>
            </form>
          )}

          {/* STEP 3: Success */}
          {step === 'success' && (
            <div className="text-center space-y-6">
              <div className="mx-auto h-16 w-16 bg-green-100 rounded-full flex items-center justify-center">
                <CheckCircle className="h-8 w-8 text-green-600" />
              </div>
              <p className="text-gray-700">{successMessage}</p>
              <Link
                to="/login"
                className="w-full inline-flex justify-center py-3 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors"
              >
                Go to Sign In
              </Link>
            </div>
          )}

          {/* Link back to login */}
          {step !== 'success' && (
            <div className="mt-6 text-center">
              <p className="text-sm text-gray-600">
                Remember your password?{' '}
                <Link to="/login" className="font-medium text-indigo-600 hover:text-indigo-500">
                  Sign in
                </Link>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
