let otpRequested = false;
let otpRequestInFlight = false;

const otpLoginForm = document.querySelector('#loginForm');
if (otpLoginForm) {
    const emailInput = otpLoginForm.querySelector('[name="email"]');
    const roleInput = otpLoginForm.querySelector('[name="role"]');
    const otpField = document.querySelector('#otpField');
    const loginButton = document.querySelector('#loginButton');
    const loginError = document.querySelector('#loginError');
    const adminOtpHint = document.querySelector('#adminOtpHint');
    const otpInput = otpField.querySelector('[name="otp"]');

    const updateAdminHint = () => {
        const isAdmin = roleInput.value === 'admin';
        adminOtpHint?.classList.toggle('hidden', !isAdmin);
        otpInput.maxLength = isAdmin ? 4 : 6;
        otpInput.pattern = isAdmin ? '[0-9]{4}' : '[0-9]{6}';
        otpInput.placeholder = isAdmin ? '4-digit code' : '6-digit code';
    };

    const resetOtp = () => {
        otpRequested = false;
        otpField.classList.add('hidden');
        loginButton.innerHTML = 'Send verification code <i class="fa-solid fa-arrow-right"></i>';
    };

    emailInput.addEventListener('input', resetOtp);
    roleInput.addEventListener('change', () => { resetOtp(); updateAdminHint(); });
    updateAdminHint();
    otpLoginForm.addEventListener('submit', async event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (otpRequestInFlight) return;
        const email = emailInput.value.trim();
        const role = roleInput.value;
        try {
            if (!otpRequested) {
                otpRequestInFlight = true;
                loginButton.disabled = true;
                await fetch('/auth/request-otp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ role, email })
                }).then(async response => {
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.detail || 'Unable to send the verification code.');
                });
                otpRequested = true;
                otpField.classList.remove('hidden');
                loginButton.innerHTML = 'Verify code <i class="fa-solid fa-arrow-right"></i>';
                loginError.textContent = 'Check your email for the verification code.';
                return;
            }

            const response = await fetch('/auth/verify-otp', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, email, otp: otpField.querySelector('[name="otp"]').value.trim() })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || 'The verification code is invalid or expired.');
            setUser(data);
            loginError.textContent = '';
        } catch (error) {
            loginError.textContent = error.message;
        } finally {
            otpRequestInFlight = false;
            loginButton.disabled = false;
        }
    }, true);
}
